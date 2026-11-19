"""Step 3: run the planner's tool calls in parallel and build the evidence pack.

- All calls start at once (shared thread pool). Each has its own time limit.
- A failed or slow tool doesn't stop the answer: the result is marked partial.
- trend_scout mode="live" is the only LLM call here (Gemini + Google Search) = LLM call 3.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field

import schemas as s
from fake_data import AS_OF, NICHE
from tools_fake import TOOLS, trend_scout_saved

READ_TIMEOUT_S = float(os.getenv("READ_TIMEOUT_S", "3"))
WEB_TIMEOUT_S = float(os.getenv("WEB_TIMEOUT_S", "8"))
WEB_MODEL = os.getenv("WEB_MODEL", os.getenv("PLANNER_MODEL", "gemini-3.5-flash-lite"))
MAX_EVIDENCE = 14

_pool = ThreadPoolExecutor(max_workers=16)  # shared by all requests, size-capped


@dataclass
class ToolRun:
    tool: str
    ms: int = 0
    status: str = "ok"          # ok | timeout | error
    note: str = ""
    count: int = 0


@dataclass
class RunResult:
    evidence: dict[str, s.Evidence] = field(default_factory=dict)
    runs: list[ToolRun] = field(default_factory=list)
    ms: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def partial(self) -> bool:
        return any(r.status != "ok" for r in self.runs)


def _live_trends(call: s.TrendScoutCall, result: RunResult) -> list[s.Evidence]:
    """LLM call 3: Gemini with Google Search grounding. Falls back to the saved nightly scan."""
    from google.genai import types
    from planner import _client_get

    q = (f"List 3 YouTube content trends from the last 7 days for this niche: {NICHE} "
         f"{('Focus: ' + call.topic) if call.topic else ''}. One line each: trend - why it is trending. No intro.")
    resp = _client_get().models.generate_content(
        model=WEB_MODEL, contents=q,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2))
    result.llm_calls += 1
    u = resp.usage_metadata
    result.tokens_in += getattr(u, "prompt_token_count", 0) or 0
    result.tokens_out += getattr(u, "candidates_token_count", 0) or 0
    lines = [l.strip(" -*•\t") for l in (resp.text or "").splitlines() if l.strip(" -*•\t")][:3]
    sources = []
    gm = resp.candidates[0].grounding_metadata if resp.candidates else None
    for ch in (getattr(gm, "grounding_chunks", None) or [])[:3]:
        if getattr(ch, "web", None) and ch.web.title:
            sources.append(ch.web.title)
    return [s.Evidence(source="trend_scout", kind="items", label="Live web trends (last 7 days)",
                       value=("sources: " + ", ".join(sources)) if sources else "", data=lines)]


def _run_one(call, result: RunResult) -> tuple[list[s.Evidence], float]:
    if call.tool == "trend_scout" and call.mode == "live":
        ev = _live_trends(call, result)
    else:
        ev = TOOLS[call.tool](call)
    return ev, time.perf_counter()


def run_plan(plan: s.AskPlan, on_done=None) -> RunResult:
    """on_done(ToolRun) is called as each tool finishes (the UI uses it for live status)."""
    result = RunResult()
    t0 = time.perf_counter()
    futures = {}
    for call in plan.calls:
        f = _pool.submit(_run_one, call, result)
        f.t0 = time.perf_counter()
        futures[f] = call

    limit = WEB_TIMEOUT_S if plan.needs_web else READ_TIMEOUT_S
    done, not_done = wait(futures, timeout=limit)

    found: list[s.Evidence] = []
    for f, call in futures.items():
        run = ToolRun(call.tool + ("(live)" if getattr(call, "mode", "") == "live" else ""))
        if f in not_done:
            run.status, run.ms, run.note = "timeout", int(limit * 1000), f"over {limit:g}s"
            if call.tool == "trend_scout":                       # live web too slow -> saved scan
                ev = trend_scout_saved(call)
                found += ev
                run.note += ", used saved trends"
        else:
            try:
                ev, t_end = f.result()
                run.ms = int((t_end - f.t0) * 1000)
                found += ev
                run.count = len(ev)
            except Exception as e:
                run.ms = int((time.perf_counter() - f.t0) * 1000)
                run.status, run.note = "error", f"{type(e).__name__}: {str(e)[:80]}"
                if call.tool == "trend_scout":
                    found += trend_scout_saved(call)
                    run.note += ", used saved trends"
        result.runs.append(run)
        if on_done:
            on_done(run)

    # Evidence pack: number every finding, add as_of, keep it small.
    for i, ev in enumerate(found[:MAX_EVIDENCE], 1):
        ev.id, ev.as_of = f"e{i}", ev.as_of or AS_OF
        result.evidence[ev.id] = ev
    result.ms = int((time.perf_counter() - t0) * 1000)
    return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    plan = s.AskPlan(decision="answer", answer_tier="premium", calls=[
        s.ChannelFactsCall(metrics=["retention", "avg_view_duration"], video_ref="latest"),
        s.ContentMemoryCall(video_ref="latest", focus="intro"),
        s.CommentAgentCall(video_ref="latest", sentiment="negative"),
    ])
    r = run_plan(plan)
    for run in r.runs:
        print(f"{run.tool:16} {run.status:7} {run.ms:4} ms  {run.count} findings {run.note}")
    print(f"total {r.ms} ms (parallel) | partial={r.partial}\n")
    for ev in r.evidence.values():
        print(f"[{ev.id}] {ev.label}: {ev.value} {('| ' + ev.compare) if ev.compare else ''}")
