"""Step 5: Ask v2 POC in the terminal.

  python cli.py

Type any question. Press 1/2 to open a button.
Commands: /plan (show/hide the planner's choices), /evidence (show what the tools found), /q (quit)
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

# Guard: VS Code auto-import can rewrite "from fake_data import X" to "from ask_v2_poc.x import X",
# which breaks when running "python cli.py". Name the broken files instead of a confusing traceback.
_here = Path(__file__).resolve().parent
_bad = [f"{p.name}:{i}" for p in _here.glob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if line.lstrip().startswith(("from ask_v2_poc", "import ask_v2_poc"))]
if _bad:
    sys.exit("These lines were changed by the editor and will break the import: " + ", ".join(_bad) +
             "\nRe-copy those files from the zip (imports must be like 'from fake_data import ...').")

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

import schemas as s
from answer import stream_answer
from fake_data import CREATOR
from planner import PLANNER_MODEL, plan_question, warmup
from runner import run_plan

APP_URL = os.getenv("APP_URL", "http://localhost:3000")

FEATURES = {
    "yt_script": ("YouTube Script Writer", "/make/yt-script"),
    "ig_script": ("Reel Script Writer", "/make/reel-script"),
    "yt_titles": ("YouTube Titles", "/make/yt-titles"),
    "yt_description_tags": ("Description & Tags", "/make/yt-description"),
    "ig_captions_hashtags": ("Captions & Hashtags", "/make/ig-captions"),
    "content_ideas": ("Content Ideas", "/make/ideas"),
    "thumbnail": ("Thumbnail Maker", "/make/thumbnail"),
    "clips": ("Clips", "/make/clips"),
    "comment_inbox": ("Comment Inbox", "/comments"),
    "next_list": ("Next List", "/next"),
    "media_kit": ("Media Kit", "/make/media-kit"),
    "video_page": ("Video Page", "/videos"),
    "reel_page": ("Reel Page", "/reels"),
}

STARTERS = ["Why did my latest video lose retention?", "What are viewers complaining about?",
            "What should I post next?"]

console = Console()
state = {"show_plan": True, "buttons": [], "chips": [], "history": [], "last_run": None}


# ---------- small render helpers ----------

def _chips(options: list[str]) -> None:
    """Suggestion chips are turned off: the user types their own questions."""
    return


def _buttons(ctas: list[s.Cta]) -> None:
    state["buttons"] = ctas[:2]
    for i, c in enumerate(ctas[:2], 1):
        name, path = FEATURES[c.feature.value]
        url = f"{APP_URL}{path}?{urlencode({kv.key: kv.value for kv in c.prefill})}"
        fill = ", ".join(f"{kv.key}={kv.value}" for kv in c.prefill)
        line = Text.assemble((f" {i} ", "bold white on magenta"), " ",
                             (f"{c.label}", f"bold magenta link {url}"), (f"  → {name}", "magenta"),
                             (f"   {fill}" if fill else "", "dim"))
        console.print(line)


def _bar(v: int, width: int = 28) -> str:
    n = round(v / 100 * width)
    return "█" * n + "░" * (width - n)


def _block(b: s.Block, ev: dict[str, s.Evidence]):
    items = [ev[i] for i in b.evidence_ids if i in ev]
    if not items:
        return None
    parts = []
    for e in items:
        if e.kind == "series" and e.data:
            t = Table.grid(padding=(0, 1))
            for p in e.data[:3] + e.data[4::2]:
                t.add_row(Text(p["x"], style="dim"), Text(_bar(p["this"]), style="red" if p["this"] < p["median"] - 5 else "green"),
                          Text(f"{p['this']}%"), Text(f"median {p['median']}%", style="dim"))
            parts += [Text(e.compare, style="dim"), t]
        elif e.kind == "stat":
            t = Table.grid(padding=(0, 2))
            t.add_row(Text(e.label, style="dim"), Text(e.value, style="bold"), Text(e.compare, style="dim"))
            parts.append(t)
        elif e.kind == "quotes":
            parts += [Text(f"  {q}", style="italic") for q in e.data[:4]]
        elif e.kind == "items":
            parts += [Text(f"  • {x}") for x in e.data[:5]]
        else:
            parts.append(Text(f"{e.value}" + (f"\n  {e.data[0]}" if e.data else ""), style=""))
    src = ", ".join(f"{e.id}" for e in items)
    return Panel(Group(*parts), title=f"[bold]{b.title}[/]", title_align="left",
                 subtitle=f"[dim]{src}[/]", subtitle_align="right", border_style="grey50", padding=(0, 1))


def _plan_panel(r) -> None:
    p = r.plan
    t = Table(box=None, padding=(0, 1), show_header=False)
    for c in p.calls:
        args = ", ".join(f"{k}={v}" for k, v in c.model_dump(exclude={"tool"}).items() if v not in ("", [], None))
        t.add_row(Text("→", style="yellow"), Text(c.tool, style="bold yellow"), Text(args, style="dim"))
    head = Text.assemble(("decision ", "dim"), (p.decision, "bold"), ("  lang ", "dim"), (p.language, "bold"),
                         ("  tier ", "dim"), (p.answer_tier, "bold"), ("  web ", "dim"), (str(p.needs_web), "bold"),
                         (f"   {r.latency_ms} ms  {r.model}", "dim"))
    body = [head]
    if p.calls:
        body.append(t)
    if p.reason:
        body.append(Text(f"why: {p.reason}", style="dim italic"))
    notes = [d[6:] for d in r.dropped if d.startswith("note: ")]
    dropped = [d for d in r.dropped if not d.startswith("note: ")]
    if notes:
        body.append(Text("model: " + "; ".join(notes), style="yellow"))
    if dropped:
        body.append(Text("dropped: " + "; ".join(dropped), style="red"))
    if r.fallback:
        body.append(Text(f"FALLBACK: {r.fallback}", style="bold red"))
    console.print(Panel(Group(*body), title="[yellow]① Planner (LLM call 1)[/]", title_align="left",
                        border_style="yellow", padding=(0, 1)))


# ---------- one turn ----------

_GREETING_RE = re.compile(
    r"^(?:hi+|hello+|hey+|heyy+|hiya|namaste|good morning|good afternoon|good evening|"
    r"how are you|how r u|thanks|thank you|thx|ty)[!.?,\s]*$",
    re.IGNORECASE,
)


def _quick_reply(q: str) -> str | None:
    """Handle pure greetings locally: zero Gemini calls and near-instant response."""
    if not _GREETING_RE.fullmatch(q.strip()):
        return None
    low = q.strip().lower()
    if low.startswith(("thank", "thx", "ty")):
        return "Anytime! 😊 I can help you understand your channel, comments, competitors, trends and content ideas."
    return ("Hey! 👋 I can help grow your YouTube and Instagram: check video/reel performance, understand "
            "viewer comments, keep an eye on competitors, spot trends and brainstorm content ideas. "
            "What are we looking at today?")


def ask(q: str) -> None:
    quick = _quick_reply(q)
    if quick:
        console.print(Rule(style="grey35"))
        console.print(Text.assemble(("You  ", "bold green"), (q, "bold")))
        console.print(Panel(Markdown(quick), title="[bold cyan]Contiq Ask · instant[/]",
                            title_align="left", border_style="cyan"))
        console.print(Text("0 LLM calls  ·  instant local reply", style="dim"))
        state["history"].append(q)
        state["buttons"] = []
        return

    t0 = time.perf_counter()
    t0 = time.perf_counter()
    console.print(Rule(style="grey35"))
    console.print(Text.assemble(("You  ", "bold green"), (q, "bold")))

    with console.status("[yellow]Understanding your question…", spinner="dots"):
        pr = plan_question(q, history=state["history"])
    plan = pr.plan
    if state["show_plan"]:
        _plan_panel(pr)
    state["history"].append(q)
    state["buttons"] = []
    llm_calls = 1

    # Replies that need no tools and no 2nd LLM call
    if plan.decision != "answer":
        hinglish = plan.language == "hinglish"
        if plan.decision == "small_talk":
            msg = ("Hey! 👋 Main aapke videos, comments, rivals aur trends dekh sakta hoon." if hinglish else
                   "Hey! 👋 I can dig into your videos, comments, rivals and trends.")
            chips = STARTERS
        elif plan.decision == "out_of_scope":
            msg = ("Yeh mere scope ke bahar hai. Main aapke channel me help karta hoon." if hinglish else
                   "That's outside what I can help with. I focus on your channel and content.")
            chips = STARTERS
        elif plan.decision == "clarify":
            msg = "Kya aapka matlab hai:" if hinglish else "Happy to help. Which one do you mean?"
            chips = plan.clarify_options or STARTERS
        elif plan.decision == "error":
            console.print(Panel(Text(plan.reply), title="[bold red]Contiq Ask[/]", title_align="left", border_style="red"))
            _footer(llm_calls, pr, None, None, t0)
            return
        else:  # hand_off
            f = plan.hand_off or s.Feature.yt_script
            msg = f"That's a job for **{FEATURES[f.value][0]}**. I'll open it for you."
            chips = []
        msg = plan.reply.strip() or msg    # the planner's own natural reply; canned text only as backup
        console.print(Panel(Markdown(msg), title="[bold cyan]Contiq Ask[/]", title_align="left", border_style="cyan"))
        if plan.decision == "hand_off":
            _buttons([s.Cta(feature=plan.hand_off or s.Feature.yt_script, label="Open",
                            prefill=[s.KV(key="topic", value=q[:60])])])
        _chips(chips)
        _footer(llm_calls, pr, None, None, t0)
        return

    # ② tools in parallel
    status_lines: list[Text] = []

    def on_done(run):
        icon, style = ("✓", "green") if run.status == "ok" else ("!", "red")
        status_lines.append(Text.assemble((f" {icon} ", style), (f"{run.tool:<20}", "bold"),
                                          (f"{run.ms:>5} ms", "dim"), (f"  {run.note}", "red")))

    with console.status(f"[blue]Checking {len(plan.calls)} sources in parallel…", spinner="dots"):
        run = run_plan(plan, on_done=on_done)
    state["last_run"] = run
    llm_calls += run.llm_calls
    console.print(Panel(Group(*status_lines), title=f"[blue]② Agents & tools (parallel, {run.ms} ms)[/]",
                        title_align="left", border_style="blue", padding=(0, 1)))

    # ③ answer, streamed
    text = ""
    gen = stream_answer(q, plan, run, state["history"][:-1])
    result = None
    title = "[bold cyan]③ Contiq Ask (LLM call 2)[/]"
    from answer import ANSWER_MODEL, ANSWER_MODEL_PREMIUM
    model = ANSWER_MODEL_PREMIUM if plan.answer_tier == "premium" else ANSWER_MODEL
    t_ans = time.perf_counter()
    waiting = lambda: Panel(Text(f"Writing your answer… ({model}, {time.perf_counter() - t_ans:.1f}s)", style="dim"),
                            title=title, title_align="left", border_style="cyan")
    with Live(waiting(), console=console, refresh_per_second=15) as live:
        import threading
        stop_tick = threading.Event()

        def tick():                          # update the timer until the first words arrive
            while not stop_tick.wait(0.2) and not text:
                live.update(waiting())
        threading.Thread(target=tick, daemon=True).start()
        try:
            while True:
                text += next(gen)
                live.update(Panel(Markdown(text), title=title, title_align="left", border_style="cyan"))
        except StopIteration as stop:
            result = stop.value
            stop_tick.set()
        except Exception as e:
            stop_tick.set()
            live.update(Panel(Text(f"Answer failed: {type(e).__name__}: {str(e)[:200]}", style="red"),
                              title=title, border_style="red"))
            _footer(llm_calls, pr, run, None, t0)
            return
    llm_calls += 1
    a = result.answer
    console.print()

    blocks = [x for x in (_block(b, run.evidence) for b in a.blocks) if x]
    for b in blocks:
        console.print(b)

    if a.action_items:
        t = Table.grid(padding=(0, 1))
        for i, it in enumerate(a.action_items, 1):
            t.add_row(Text("☐", style="green"), Text(it.text),
                      Text(" ".join(it.evidence_ids), style="dim"))
        console.print(Panel(t, title="[bold green]Action items[/]", title_align="left", border_style="green", padding=(0, 1)))

    if a.ctas:
        _buttons(a.ctas)
    _chips(a.follow_ups)
    _footer(llm_calls, pr, run, result, t0)


def _footer(llm_calls, pr, run, res, t0) -> None:
    total = int((time.perf_counter() - t0) * 1000)
    bits = [f"{llm_calls} LLM call{'s' if llm_calls > 1 else ''}", f"planner {pr.latency_ms} ms"]
    tin, tout = pr.tokens_in, pr.tokens_out
    if run:
        bits.append(f"tools {run.ms} ms")
        tin, tout = tin + run.tokens_in, tout + run.tokens_out
    if res:
        bits.append(f"first word {(pr.latency_ms + run.ms + res.first_token_ms) / 1000:.1f} s")
        bits.append(res.model)
        tin, tout = tin + res.tokens_in, tout + res.tokens_out
    bits += [f"total {total / 1000:.1f} s", f"tokens {tin:,} in / {tout:,} out"]
    line = Text("  ·  ".join(bits), style="dim")
    if res:
        if res.unverified_numbers:
            line.append(f"  ·  ⚠ unverified numbers: {', '.join(res.unverified_numbers)}", style="yellow")
        else:
            line.append("  ·  ✓ numbers checked", style="green")
        if res.problems:
            line.append("  ·  " + "; ".join(res.problems), style="yellow")
        if run and run.partial:
            line.append("  ·  partial data", style="yellow")
    console.print(line)


def open_button(i: int) -> None:
    c = state["buttons"][i]
    name, path = FEATURES[c.feature.value]
    params = {kv.key: kv.value for kv in c.prefill}
    t = Table(box=None, show_header=False, padding=(0, 2))
    for k, v in params.items():
        t.add_row(Text(k, style="dim"), Text(v, style="bold"))
    url = f"{APP_URL}{path}?{urlencode(params)}"
    console.print(Panel(Group(Text(f"Opening {name} with these inputs filled in:"), t,
                              Text(url, style=f"underline magenta link {url}")),
                        title=f"[magenta]▶ {c.label}[/]", title_align="left", border_style="magenta"))


def show_evidence() -> None:
    run = state["last_run"]
    if not run:
        console.print("[dim]No evidence yet.[/]")
        return
    t = Table(title="Evidence pack (what the answer LLM saw)", show_lines=False)
    for col in ("id", "source", "label", "value", "compare"):
        t.add_column(col, overflow="fold")
    for e in run.evidence.values():
        t.add_row(e.id, e.source, e.label, e.value, e.compare)
    console.print(t)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        console.print("[red]Set GEMINI_API_KEY in this terminal first.[/]")
        return
    console.print(Panel(Text.assemble(
        ("Contiq Ask v2 · POC  ", "bold"), ("your YouTube & Instagram growth assistant\n", ""),
        (f"Demo channel (fake data): {CREATOR['name']} · niche: {CREATOR['niche']}\n", "dim"),
        (f"planner {PLANNER_MODEL} → parallel agents → answer model\n", "dim"),
        ("Analytics · comments · competitors · trends · ideas  |  /plan /evidence /q", "dim")),
        border_style="cyan"))
    with console.status("[dim]Connecting to Gemini…", spinner="dots"):
        try:
            warmup()
        except Exception as e:
            console.print(f"[red]Gemini warm-up failed: {type(e).__name__}: {str(e)[:200]}[/]")
    from answer import ANSWER_BACKUP_MODEL, ANSWER_MODEL
    from planner import AVAILABLE, PLANNER_BACKUP_MODEL, model_chain
    console.print(Text(f"planner: {' → '.join(model_chain(PLANNER_MODEL, PLANNER_BACKUP_MODEL, True))}   "
                       f"answer: {' → '.join(model_chain(ANSWER_MODEL, ANSWER_BACKUP_MODEL, False))}", style="dim"))
    if AVAILABLE and PLANNER_MODEL not in AVAILABLE:
        console.print(f"[yellow]Your key doesn't have {PLANNER_MODEL}. Available: {', '.join(AVAILABLE)}[/]")
    while True:
        try:
            q = console.input("\n[bold green]ask›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        low = q.lower()
        if low in ("/q", "q", "quit", "exit"):
            break
        if low == "/plan":
            state["show_plan"] = not state["show_plan"]
            console.print(f"[dim]planner view {'on' if state['show_plan'] else 'off'}[/]")
            continue
        if low == "/evidence":
            show_evidence()
            continue
        if low in ("1", "2") and int(low) <= len(state["buttons"]):
            open_button(int(low) - 1)
            continue
        try:
            ask(q)
        except Exception as e:
            console.print(f"[red]Error: {type(e).__name__}: {e}[/]")


if __name__ == "__main__":
    main()
