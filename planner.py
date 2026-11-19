"""Step 2: the planner (LLM call 1).

Reads the question + catalog, returns a validated AskPlan.
No regex routing. If the LLM fails or times out, we use a safe Basic Facts plan.

Run:
  python planner.py --dry                      # no API call: shows prompt + schema size
  python planner.py "why did my latest video lose retention"
  python planner.py --check                    # finds which Gemini setting fails
  python planner.py --samples                  # runs 9 test questions
  python planner.py                            # interactive: type any question
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from pydantic import TypeAdapter, ValidationError

import schemas as s
from catalog import CATALOG, planner_catalog
from fake_data import CREATOR

PLANNER_MODEL = os.getenv("PLANNER_MODEL", "gemini-3.5-flash-lite")
# Production target is 1.2 s. POC default is looser so first runs don't time out.
# If the main model is busy (503) or slow, we switch to the backup model right away.
# Empty = pick automatically from the models your key has (see discover_models).
PLANNER_BACKUP_MODEL = os.getenv("PLANNER_BACKUP_MODEL", "")
PLANNER_TIMEOUT_S = float(os.getenv("PLANNER_TIMEOUT_S", "8"))   # total, all models
PLANNER_HEDGE_S = float(os.getenv("PLANNER_HEDGE_S", "2"))       # no reply by then -> also ask the backup

_call_adapter = TypeAdapter(s.ToolCall)
_pool = ThreadPoolExecutor(max_workers=8)
_client = None


# ---------- 1. the schema we send to Gemini ----------
# Gemini handles flat schemas best, so each call is {tool: enum, ...all possible inputs, optional}.
# After the reply, every call is checked against its real Pydantic class (schemas.py).

def _wire_schema(platform: str) -> dict:
    tools = [n for n, t in CATALOG.items() if t.platform in (platform, "both")]
    arg_props: dict = {}
    for name in tools:
        for k, v in CATALOG[name].args.model_json_schema().get("properties", {}).items():
            if k == "tool":
                continue
            v = {kk: vv for kk, vv in v.items() if kk in ("type", "enum", "items", "description")}
            arg_props.setdefault(k, v)
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["answer", "small_talk", "clarify", "out_of_scope", "hand_off"]},
            "language": {"type": "string", "enum": ["en", "hi", "hinglish"]},
            "answer_tier": {"type": "string", "enum": ["default", "premium"]},
            "calls": {
                "type": "array", "maxItems": 6,
                "items": {"type": "object",
                          "properties": {"tool": {"type": "string", "enum": tools}, **arg_props},
                          "required": ["tool"]},
            },
            "clarify_options": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
            "hand_off": {"type": "string", "enum": [f.value for f in s.Feature]},
            "reply": {"type": "string", "description": "only when decision is not answer"},
            "reason": {"type": "string"},
        },
        "required": ["decision", "language", "answer_tier", "calls"],
    }


# ---------- 2. the prompt ----------
# Static part first (rules + catalog) so Gemini's implicit cache can reuse it across users.

RULES = """You are the planner for Contiq Ask. Contiq helps creators grow their YouTube and Instagram.
Read the creator's message. Pick ONE decision from DECISIONS, then fill its inputs.
For decision "answer", pick tools from TOOLS. You do NOT write the final answer yourself.

Tool rules:
- Pick the FEWEST tools that answer the question. Usually 1-3, max 6.
- Fill inputs using only the allowed values.
- video_ref: "latest" = newest video, "1".."10" = position in RECENT VIDEOS, "channel" = whole channel.
  Never write a video ID or title.
- "why did X drop/lose" questions: numbers tool + content_memory (+ comment_agent if viewers' reaction matters).
- trend_scout mode "live" only if the user asks for today / this week / latest trends. Otherwise "saved".

Always fill:
- language: "en", "hi" (Devanagari), or "hinglish" (Hindi in Latin letters, e.g. "views kyu gir rahe").
- answer_tier: "premium" only for diagnosis ("why...") or strategy advice, else "default".
- reason: one short line on why you chose this.

"""


def _build_prompt(question: str, platform: str, history: list[str]) -> tuple[str, str]:
    system = RULES + planner_catalog(platform)
    videos = "\n".join(f"  {i}. {t}" for i, t in enumerate(CREATOR["recent_videos"], 1))
    user = (
        f"PLATFORM: {platform}\n"
        f"NICHE: {CREATOR['niche']}\n"
        f"RECENT VIDEOS (1 = latest):\n{videos}\n"
        f"TRACKED RIVALS: {', '.join(CREATOR['rivals'])}\n"
        f"LAST TURNS: {' | '.join(history[-3:]) or 'none'}\n\n"
        # Fence the user's text so it can't pose as instructions.
        f"<question untrusted=\"true\">\n{question.strip()[:500]}\n</question>"
    )
    return system, user


# ---------- 2b. small input cleanup + safe Gemini response parsing ----------

_GREETING_PREFIX = re.compile(
    r"^(?:(?:h+i+|he+y+|hello+|namaste|good\s+morning|good\s+afternoon|good\s+evening)"
    r"[\s,!.:-]*)+",
    re.IGNORECASE,
)


def clean_question(question: str) -> str:
    """Remove only a leading greeting; preserve the user's actual request."""
    cleaned = _GREETING_PREFIX.sub("", question.strip()).strip()
    return cleaned or question.strip()


def _response_text(response) -> str:
    """Read Gemini text without relying on response.text (which can raise IndexError)."""
    try:
        value = response.text
        if value and value.strip():
            return value.strip()
    except (IndexError, AttributeError, ValueError):
        pass

    pieces = []
    for candidate in (getattr(response, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            value = getattr(part, "text", None)
            if value:
                pieces.append(value)
    text = "\n".join(pieces).strip()
    if text:
        return text

    reasons = []
    for candidate in (getattr(response, "candidates", None) or []):
        reason = getattr(candidate, "finish_reason", None)
        if reason:
            reasons.append(str(reason))
    suffix = f" (finish_reason={','.join(reasons)})" if reasons else ""
    raise ValueError("Gemini returned no usable text candidate" + suffix)


def _parse_json_object(text: str) -> dict:
    """Accept raw JSON or fenced JSON; reject empty/malformed model output."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Planner response did not contain a JSON object")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("Planner JSON must be an object")
    return value


# ---------- 3. checks after the reply ----------

@dataclass
class PlanResult:
    plan: s.AskPlan
    latency_ms: int
    fallback: str = ""                      # why we fell back, empty if we didn't
    dropped: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


def basic_facts_plan(platform: str, why: str) -> s.AskPlan:
    call = (s.ChannelFactsCall(metrics=["views", "retention"]) if platform == "youtube"
            else s.AccountFactsCall(metrics=["reach", "plays"]))
    return s.AskPlan(decision="answer", calls=[call], reason=f"fallback: {why}"[:200])


def emergency_plan(question: str, platform: str, why: str) -> s.AskPlan:
    """Very small deterministic safety net for common metric questions if Gemini fails."""
    q = question.lower()
    if platform == "youtube" and any(w in q for w in ("view", "views", "view count")):
        latest = any(w in q for w in ("latest", "last", "newest", "recent", "video", "vid"))
        call = s.ChannelFactsCall(metrics=["views"], period="7d",
                                  video_ref="latest" if latest else "channel")
        return s.AskPlan(decision="answer", calls=[call], reason=f"safe metric fallback: {why}"[:200])
    if platform == "instagram" and any(w in q for w in ("reach", "plays", "views", "reel")):
        call = s.AccountFactsCall(metrics=["reach", "plays"], period="7d",
                                  video_ref="latest" if "reel" in q or "latest" in q else "channel")
        return s.AskPlan(decision="answer", calls=[call], reason=f"safe metric fallback: {why}"[:200])
    return s.AskPlan(decision="error", reply=(
        "I couldn't get a usable planner response from Gemini just now. Please try again in a moment."
    ), reason=f"planner failed: {why}"[:200])


def _validate(raw: dict, platform: str) -> tuple[s.AskPlan, list[str]]:
    dropped, calls, seen, live_used = [], [], set(), False
    for c in raw.get("calls") or []:
        c = {k: v for k, v in c.items() if v not in (None, "", [])} | {"tool": c.get("tool")}
        name = c.get("tool")
        spec = CATALOG.get(name)
        if not spec or spec.platform not in (platform, "both"):
            dropped.append(f"{name}: not allowed on {platform}")
            continue
        # keep only inputs this tool really has
        allowed = set(spec.args.model_fields)
        c = {k: v for k, v in c.items() if k in allowed}
        try:
            call = _call_adapter.validate_python(c)
        except ValidationError as e:
            dropped.append(f"{name}: {e.errors()[0]['msg']}")
            continue
        if getattr(call, "mode", None) == "live":
            if live_used:
                dropped.append(f"{name}: only 1 live web call allowed")
                continue
            live_used = True
        key = call.model_dump_json()
        if key not in seen:
            seen.add(key)
            calls.append(call)
    raw = {**raw, "calls": calls}
    if raw.get("hand_off") not in {f.value for f in s.Feature}:
        raw["hand_off"] = None
    return s.AskPlan.model_validate(raw), dropped


# ---------- 4. the call ----------

# Thinking setting differs by model family, and a wrong one gives a bare 400.
#   ""        -> don't send it (model default, always accepted)   <- default
#   "0"       -> thinking_budget=0 (Gemini 2.5: thinking off, fastest)
#   "minimal" / "low" -> thinking_level (Gemini 3.x)
PLANNER_THINKING = os.getenv("PLANNER_THINKING", "minimal")


def _client_get():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        # attempts=1: no silent retries. A busy model fails fast and we switch to the backup model.
        _client = genai.Client(http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))
    return _client


def thinking_config(model: str, level: str):
    """Gemini 2.x: thinking_budget=0 (off). Gemini 3.x: thinking_level ('minimal', 'low')."""
    from google.genai import types
    if not level:
        return None
    if level.isdigit():
        return types.ThinkingConfig(thinking_budget=int(level))
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level=level)


AVAILABLE: list[str] = []      # filled by discover_models() at startup


def discover_models() -> list[str]:
    """Ask Gemini which text models this key can use (so backups never point at a 404 model)."""
    skip = ("tts", "image", "live", "audio", "embedding", "vision", "learnlm", "gemma", "robotics", "computer")
    names = []
    for m in _client_get().models.list():
        n = (m.name or "").removeprefix("models/")
        if "generateContent" in (m.supported_actions or []) and "flash" in n and not any(x in n for x in skip):
            names.append(n)
    AVAILABLE[:] = names
    return names


def model_chain(primary: str, backup: str, lite: bool) -> list[str]:
    """Primary first, then the backup. With no backup set, pick another available flash(-lite) model."""
    chain = [primary]
    if backup:
        chain.append(backup)
    else:
        same_kind = [n for n in AVAILABLE if ("lite" in n) == lite and n != primary]
        other = [n for n in AVAILABLE if n != primary and n not in same_kind]
        chain += (same_kind or other)[:1]
    if AVAILABLE:                                   # drop names the key doesn't have
        chain = [m for m in chain if m in AVAILABLE] or chain
    return list(dict.fromkeys(chain))


def call_with_backup(models: list[str], fn, timeout_s: float, hedge_s: float):
    """Ask the first model. If it errors, or hasn't replied after hedge_s, ALSO ask the next model.
    Whichever answers first wins. Returns (result, model, notes)."""
    models = list(dict.fromkeys(m for m in models if m))
    notes, pending, nxt, t0 = [], {}, 0, time.perf_counter()

    def launch():
        nonlocal nxt
        pending[_pool.submit(fn, models[nxt])] = models[nxt]
        nxt += 1

    launch()
    while pending:
        left = timeout_s - (time.perf_counter() - t0)
        if left <= 0:
            break
        done, _ = wait(pending, timeout=min(left, hedge_s) if nxt < len(models) else left,
                       return_when=FIRST_COMPLETED)
        if not done:                                # slow -> start the backup in parallel
            notes.append(f"{', '.join(pending.values())} slow (>{time.perf_counter() - t0:.0f}s), also asked {models[nxt]}")
            launch()
            continue
        for f in done:
            m = pending.pop(f)
            try:
                return f.result(), m, notes
            except Exception as e:
                notes.append(f"{m}: {type(e).__name__} {str(e)[:70]}")
                if nxt < len(models):
                    launch()
    notes += [f"{m}: no reply in {timeout_s:g}s" for m in pending.values()]
    raise RuntimeError("; ".join(notes))


def warmup() -> None:
    """Open the connection once at startup so the first question isn't slow (~3 s on Windows)."""
    from google.genai import types
    try:
        discover_models()
    except Exception:
        pass                                        # listing failed: keep the configured names
    cfg = types.GenerateContentConfig(max_output_tokens=5)
    call_with_backup(model_chain(PLANNER_MODEL, PLANNER_BACKUP_MODEL, lite=True),
                     lambda m: _client_get().models.generate_content(model=m, contents="ok", config=cfg), 15, 3)


def _gen_config(system: str, platform: str, thinking: str = PLANNER_THINKING, schema: bool = True,
                model: str = PLANNER_MODEL):
    from google.genai import types
    cfg = dict(system_instruction=system, temperature=0, response_mime_type="application/json")
    if schema:
        cfg["response_json_schema"] = _wire_schema(platform)
    tc = thinking_config(model, thinking)
    if tc:
        cfg["thinking_config"] = tc
    return types.GenerateContentConfig(**cfg)


def plan_question(question: str, platform: str = "youtube", history: list[str] | None = None) -> PlanResult:
    """Plan a request. Parsing/validation happens inside the retry so bad model output retries."""
    question = clean_question(question)
    system, user = _build_prompt(question, platform, history or [])
    t0 = time.perf_counter()

    def generate_and_validate(model: str):
        response = _client_get().models.generate_content(
            model=model,
            contents=user,
            config=_gen_config(system, platform, model=model),
        )
        raw_text = _response_text(response)
        raw_json = _parse_json_object(raw_text)
        plan, dropped = _validate(raw_json, platform)
        return plan, dropped, response

    try:
        chain = model_chain(PLANNER_MODEL, PLANNER_BACKUP_MODEL, lite=True)
        # Keep the configured primary even if model discovery was unavailable/incomplete.
        if PLANNER_MODEL not in chain:
            chain.insert(0, PLANNER_MODEL)
        result_data, model, errors = call_with_backup(
            chain, generate_and_validate, PLANNER_TIMEOUT_S, PLANNER_HEDGE_S
        )
        plan, dropped, response = result_data
        ms = int((time.perf_counter() - t0) * 1000)
        usage = getattr(response, "usage_metadata", None)
        result = PlanResult(
            plan, ms,
            dropped=dropped + [f"note: {e}" for e in errors],
            tokens_in=getattr(usage, "prompt_token_count", 0) or 0,
            tokens_out=getattr(usage, "candidates_token_count", 0) or 0,
            model=model,
        )
        if plan.decision == "answer" and not plan.calls:
            result.plan = basic_facts_plan(platform, "planner selected answer without tools")
            result.fallback = "planner selected answer without tools"
        return result

    except Exception as exc:
        ms = int((time.perf_counter() - t0) * 1000)
        why = f"{type(exc).__name__}: {str(exc)[:180]}"
        # For simple metric questions, don't turn a transient Gemini issue into a dead end.
        plan = emergency_plan(question, platform, why)
        return PlanResult(plan, ms, fallback=why, model="local-fallback")


# ---------- 5. run from terminal ----------

SAMPLES = [
    "hi",
    "hii what r my latest vid views",
    "why did my latest video lose retention",
    "mere views kyu gir rahe hain",
    "help",
    "what's the weather in Mumbai",
    "what's trending in my niche this week",
    "how am I doing vs Paisa Talks",
    "what are people complaining about in the SIP vs FD video",
    "write me a script for my next video",
]


def _print(q: str, r: PlanResult) -> None:
    p = r.plan
    print(f"\nQ: {q}")
    print(f"   {p.decision} | lang={p.language} | tier={p.answer_tier} | web={p.needs_web} | "
          f"{r.latency_ms} ms | tokens {r.tokens_in}/{r.tokens_out}")
    for c in p.calls:
        print("   ->", c.model_dump_json(exclude={"tool"}).replace('"', ""), f"[{c.tool}]")
    if p.clarify_options:
        print("   options:", p.clarify_options)
    if p.reason:
        print("   reason:", p.reason)
    if r.dropped:
        print("   dropped:", r.dropped)
    if r.fallback:
        print("   FALLBACK:", r.fallback)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows terminal: show ₹ and Hindi correctly
    args = sys.argv[1:]
    if args and args[0] == "--dry":
        sys_p, user_p = _build_prompt(SAMPLES[0], "youtube", [])
        print(sys_p, "\n\n---- USER ----\n", user_p)
        print(f"\nschema: {len(json.dumps(_wire_schema('youtube')))} chars | prompt: ~{(len(sys_p)+len(user_p))//4} tokens")
        # offline check of the validator
        plan, dropped = _validate({"decision": "answer", "language": "en", "answer_tier": "premium", "calls": [
            {"tool": "channel_facts", "metrics": ["retention"], "video_ref": "latest", "focus": "intro"},
            {"tool": "account_facts", "metrics": ["reach"]},
            {"tool": "content_memory", "video_ref": "abc123"},
        ]}, "youtube")
        print("validator kept:", [c.tool for c in plan.calls], "| dropped:", dropped)
    elif not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        print("Set GEMINI_API_KEY in this terminal first.")
    elif args and args[0] == "--check":
        # Finds which request setting Gemini rejects. Each line adds one setting.
        from google.genai import types
        sys_p, user_p = _build_prompt("hi", "youtube", [])
        steps = [
            ("plain text", types.GenerateContentConfig(temperature=0)),
            ("+ system prompt", types.GenerateContentConfig(system_instruction=sys_p, temperature=0)),
            ("+ JSON mode", _gen_config(sys_p, "youtube", thinking="", schema=False)),
            ("+ JSON schema", _gen_config(sys_p, "youtube", thinking="")),
            ("+ thinking_budget=0", _gen_config(sys_p, "youtube", thinking="0")),
            ("+ thinking_level=minimal", _gen_config(sys_p, "youtube", thinking="minimal")),
            ("+ thinking_level=low", _gen_config(sys_p, "youtube", thinking="low")),
        ]
        print(f"model: {PLANNER_MODEL}")
        for name, cfg in steps:
            t = time.perf_counter()
            try:
                r = _client_get().models.generate_content(model=PLANNER_MODEL, contents=user_p, config=cfg)
                print(f"  OK    {name:26} {int((time.perf_counter()-t)*1000):5} ms  {(r.text or '')[:60]!r}")
            except Exception as e:
                print(f"  FAIL  {name:26} {str(e)[:150]}")
    elif args and args[0] == "--samples":
        for q in SAMPLES:
            _print(q, plan_question(q))
    elif args:
        q = " ".join(args)
        _print(q, plan_question(q))
    else:
        # interactive: type any question, see the plan. Empty line or "q" to quit.
        print(f"Planner: {PLANNER_MODEL} | platform: youtube | type a question (q to quit)")
        history: list[str] = []
        while True:
            try:
                q = input("\nask> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("", "q", "quit", "exit"):
                break
            _print(q, plan_question(q, history=history))
            history.append(q)
