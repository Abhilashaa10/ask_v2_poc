"""Step 4: the answer LLM (LLM call 2).

One streamed call:
  1. the summary text streams first (user sees words fast)
  2. then a marker line, then JSON: blocks, action_items, ctas, follow_ups
Code validates the JSON (AskAnswer), drops anything pointing to unknown evidence,
and checks every number in the summary exists in the evidence.
"""
from __future__ import annotations

import json
import os
import re
import itertools
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

import schemas as s
from fake_data import CREATOR
from planner import _client_get, call_with_backup, model_chain, thinking_config
from runner import RunResult

ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gemini-3.5-flash")
# Same model for every answer by default (fastest). Set ANSWER_MODEL_PREMIUM to use a bigger one for "why" questions.
ANSWER_MODEL_PREMIUM = os.getenv("ANSWER_MODEL_PREMIUM", ANSWER_MODEL)
# Gemini 3.x "thinks" before writing by default, which delays the first word by many seconds.
ANSWER_THINKING = os.getenv("ANSWER_THINKING", "minimal")
# If the main model is busy (503) or doesn't start writing in time, switch to this one.
ANSWER_BACKUP_MODEL = os.getenv("ANSWER_BACKUP_MODEL", "")      # empty = pick from your key's models
FIRST_WORD_TIMEOUT_S = float(os.getenv("FIRST_WORD_TIMEOUT_S", "12"))   # total wait for the first word
ANSWER_HEDGE_S = float(os.getenv("ANSWER_HEDGE_S", "4"))                # no first word by then -> also ask backup
MARKER = "@@EXTRAS@@"

FEATURES_HELP = """yt_script: YouTube script writer (prefill: video_ref, section, goal)
ig_script: Reel script writer (prefill: topic, goal)
yt_titles: YouTube title generator (prefill: video_ref or topic)
yt_description_tags: description + tags (prefill: video_ref)
ig_captions_hashtags: captions + hashtags (prefill: topic)
content_ideas: idea generator (prefill: topic, count)
thumbnail: thumbnail maker (prefill: video_ref, idea)
clips: clip finder (prefill: video_ref)
comment_inbox: reply to comments (prefill: video_ref, filter)
next_list: plan next uploads (prefill: idea)
media_kit: media kit (no prefill)
video_page: open a video's analytics page (prefill: video_ref)
reel_page: open a reel's analytics page (prefill: video_ref)"""

SYSTEM = f"""You are Contiq Ask, the creator's growth coach inside Contiq (a platform that helps creators grow on
YouTube and Instagram). Sound like a warm, smart friend who is a growth expert.
Talk to the creator like a person would in chat, not like a report.

Facts: use ONLY the EVIDENCE. Every number you write must appear in the evidence. Never invent data.
If something is missing or a tool failed, say so naturally ("I couldn't see your comments this time, but...").

Write in two parts.

PART 1: the answer, in a natural conversational voice.
- Start by answering directly, the way a friend would ("Okay, so here's what happened..."). No "Based on the data".
- Explain the why in plain words, connecting the dots between the numbers, what's in the video, and what viewers said.
- 1-2 short paragraphs (roughly 60-110 words). Use a short bullet list only if it really reads better.
- Be honest but encouraging. Point out what's working too, if the evidence shows it.
- Bold only the key numbers with **...**. No headings, no JSON in part 1.
- Do not repeat the action items here in full; they are shown separately.

Then a line with exactly: {MARKER}

PART 2: one JSON object, nothing else:
{{
 "blocks": [{{"type": "stat|chart|list|quote", "title": "...", "evidence_ids": ["e1"]}}],   // max 4; chart only for kind=series
 "action_items": [{{"text": "specific step the creator can do this week", "evidence_ids": ["e2"]}}],  // max 3
 "ctas": [{{"feature": "<feature>", "label": "2-4 words", "prefill": [{{"key": "...", "value": "..."}}]}}]  // max 2
}}
Keep PART 2 short: max 2 blocks, max 3 action items.

Allowed ctas features (pick the one that helps them act on your advice):
{FEATURES_HELP}
For video_ref use "latest" or "1".."10" (position in recent videos), never a title.
"""

LANG_RULE = {"en": "Reply in English.",
             "hi": "Reply in Hindi (Devanagari).",
             "hinglish": "Reply in Hinglish (Hindi in Latin letters, mixed with English), like the user."}


@dataclass
class AnswerResult:
    answer: s.AskAnswer | None = None
    model: str = ""
    first_token_ms: int = 0
    ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    problems: list[str] = field(default_factory=list)   # validation notes shown in the footer
    unverified_numbers: list[str] = field(default_factory=list)


def _evidence_text(run: RunResult) -> str:
    rows = []
    for e in run.evidence.values():
        d = e.model_dump(exclude={"id", "source", "as_of"}, exclude_defaults=True)
        rows.append(f"[{e.id}] ({e.source}, as of {e.as_of}) {json.dumps(d, ensure_ascii=False)}")
    if run.partial:
        rows.append("NOTE: some tools failed or timed out: " +
                    ", ".join(f"{r.tool} ({r.status})" for r in run.runs if r.status != "ok"))
    return "\n".join(rows) or "(no evidence)"


def _check_numbers(summary: str, run: RunResult) -> list[str]:
    """Every number in the summary must appear somewhere in the evidence."""
    blob = json.dumps([e.model_dump() for e in run.evidence.values()], ensure_ascii=False).replace(",", "")
    bad = []
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", summary):
        if n.replace(",", "") not in blob and n not in ("1", "2", "3"):
            bad.append(n)
    return bad



def _safe_chunk_text(chunk) -> str:
    """Gemini can emit a chunk with no candidate/content; its .text property may raise IndexError."""
    try:
        return chunk.text or ""
    except (IndexError, AttributeError, ValueError):
        return ""


def _evidence_fallback(run: RunResult) -> s.AskAnswer:
    """Never crash the user-facing flow: summarize only facts returned by tools."""
    stats = [e for e in run.evidence.values() if e.kind == "stat"]
    if stats:
        lines = []
        for e in stats[:3]:
            line = f"{e.label}: **{e.value}**"
            if e.compare:
                line += f" ({e.compare})"
            lines.append(line)
        summary = "Here's what I could verify from your channel data: " + "; ".join(lines) + "."
        blocks = [
            s.Block(type="stat", title=e.label[:60], evidence_ids=[e.id])
            for e in stats[:3] if e.id
        ]
        return s.AskAnswer(summary=summary, blocks=blocks)
    if run.evidence:
        first = next(iter(run.evidence.values()))
        detail = first.value or ", ".join(str(x) for x in first.data[:3])
        summary = f"I retrieved {first.label.lower()}. {detail}".strip()
    else:
        summary = "I couldn't retrieve channel data for that question just now. Please try again."
    return s.AskAnswer(summary=summary)


def stream_answer(question: str, plan: s.AskPlan, run: RunResult, history: list[str] | None = None):
    """Generator: yields summary text chunks as they arrive. Returns AnswerResult (via StopIteration.value)."""
    res = AnswerResult(model=ANSWER_MODEL_PREMIUM if plan.answer_tier == "premium" else ANSWER_MODEL)
    videos = "\n".join(f"  {i}. {t}" for i, t in enumerate(CREATOR["recent_videos"], 1))
    user = (f"RECENT VIDEOS (1 = latest):\n{videos}\n"
            f"LAST TURNS: {' | '.join((history or [])[-3:]) or 'none'}\n\n"
            f"EVIDENCE:\n{_evidence_text(run)}\n\n"
            f"<question untrusted=\"true\">\n{question.strip()[:500]}\n</question>\n\n"
            f"{LANG_RULE[plan.language]}")
    from google.genai import types

    def start(model: str):
        """Open the stream and wait for the first chunk (so a busy model fails here, not mid-answer)."""
        cfg = types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0.3,
                                          thinking_config=thinking_config(model, ANSWER_THINKING))
        it = iter(_client_get().models.generate_content_stream(model=model, contents=user, config=cfg))
        return it, next(it)

    t0 = time.perf_counter()
    text, shown, usage = "", 0, None
    try:
        chain = model_chain(res.model, ANSWER_BACKUP_MODEL, lite=False)
        if ANSWER_MODEL not in chain:
            chain.insert(1, ANSWER_MODEL)
        (iter_, first), res.model, errors = call_with_backup(
            chain, start, FIRST_WORD_TIMEOUT_S, ANSWER_HEDGE_S)
        res.problems += errors

        for chunk in itertools.chain([first], iter_):
            if not res.first_token_ms:
                res.first_token_ms = int((time.perf_counter() - t0) * 1000)
            usage = getattr(chunk, "usage_metadata", None) or usage
            chunk_text = _safe_chunk_text(chunk)
            if not chunk_text:
                continue
            text += chunk_text
            # Stream only the answer; keep the marker and JSON hidden.
            cut = text.find(MARKER)
            safe = cut if cut >= 0 else max(shown, len(text) - len(MARKER))
            if safe > shown:
                yield text[shown:safe]
                shown = safe
    except Exception as exc:
        res.problems.append(f"answer model failed: {type(exc).__name__}: {str(exc)[:100]}")
        # If Gemini failed before producing words, return a useful evidence-based answer.
        if not text.strip():
            fallback = _evidence_fallback(run)
            res.answer = fallback
            res.ms = int((time.perf_counter() - t0) * 1000)
            res.first_token_ms = res.ms
            yield fallback.summary
            return res
        # If the stream failed mid-response, append verified facts rather than crashing.
        fallback = _evidence_fallback(run)
        text += "\n\n" + fallback.summary
        yield "\n\n" + fallback.summary

    res.ms = int((time.perf_counter() - t0) * 1000)
    res.tokens_in = getattr(usage, "prompt_token_count", 0) or 0
    res.tokens_out = getattr(usage, "candidates_token_count", 0) or 0

    summary, _, extras = text.partition(MARKER)
    if not _ and shown < len(text):          # no marker at all: show the rest as text
        yield text[shown:]
    summary = summary.strip()
    if not summary:
        fallback = _evidence_fallback(run)
        res.answer = fallback
        res.problems.append("Gemini returned no answer text; used evidence fallback")
        return res

    extras_obj: dict = {}
    if extras.strip():
        raw = extras.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            extras_obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except Exception:
            res.problems.append("extras JSON unreadable, showing text only")
    else:
        res.problems.append("no extras returned")

    # Keep only parts that point at real evidence and real features.
    ids = set(run.evidence)
    extras_obj["blocks"] = [b for b in extras_obj.get("blocks", [])
                            if isinstance(b, dict) and set(b.get("evidence_ids", [])) & ids][:4]
    for a in extras_obj.get("action_items", []):
        if isinstance(a, dict):
            a["evidence_ids"] = [i for i in a.get("evidence_ids", []) if i in ids]
    good_ctas = []
    for c in extras_obj.get("ctas", [])[:2]:
        try:
            good_ctas.append(s.Cta.model_validate(c))
        except ValidationError:
            res.problems.append(f"dropped button with unknown feature: {c.get('feature') if isinstance(c, dict) else c}")
    extras_obj["ctas"] = good_ctas
    for k, n in (("action_items", 3), ("follow_ups", 3)):
        extras_obj[k] = extras_obj.get(k, [])[:n]
    try:
        res.answer = s.AskAnswer.model_validate({**extras_obj, "summary": summary})
    except ValidationError as e:
        res.problems.append(f"extras invalid: {e.errors()[0]['loc']}")
        res.answer = s.AskAnswer(summary=summary)

    res.unverified_numbers = _check_numbers(summary, run)
    return res
