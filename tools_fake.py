"""Step 3: fake tool implementations. Same inputs/outputs as the real ones will have.

Each function takes its call (from schemas.py) and returns a list of Evidence.
No LLM calls here, except trend_scout mode="live" (the 3rd LLM call), handled in runner.py.
Swap these for real DB / Weaviate reads later; nothing else changes.
"""
from __future__ import annotations

import random
import time

import fake_data as d
import schemas as s

E = s.Evidence


def _video(ref: str) -> dict | None:
    if ref == "channel":
        return None
    i = 0 if ref == "latest" else int(ref) - 1
    return d.VIDEOS[i] if i < len(d.VIDEOS) else None


def _pct_change(now: float, before: float) -> str:
    return f"{(now - before) / before * 100:+.0f}%"


def _latency():
    time.sleep(random.uniform(0.05, 0.25))  # pretend DB/Weaviate time


def channel_facts(c: s.ChannelFactsCall) -> list[E]:
    _latency()
    v, out = _video(c.video_ref), []
    if v:
        t = v["title"]
        rows = {
            "views": E(source=c.tool, kind="stat", label=f"Views: {t}", value=f"{v['views']:,}",
                       compare=f"channel median {v['views_median']:,} ({_pct_change(v['views'], v['views_median'])})"),
            "retention": E(source=c.tool, kind="stat", label=f"Avg retention: {t}", value=f"{v['retention']}%",
                           compare=f"channel median {v['retention_median']}%"),
            "avg_view_duration": E(source=c.tool, kind="stat", label=f"Avg view duration: {t}", value=v["avd"],
                                   compare=f"channel median {v['avd_median']} (video length {v['length']})"),
            "ctr": E(source=c.tool, kind="stat", label=f"CTR: {t}", value=f"{v['ctr']}%",
                     compare=f"channel median {v['ctr_median']}%"),
        }
        out = [rows[m] for m in c.metrics if m in rows]
        if "retention" in c.metrics or "avg_view_duration" in c.metrics:
            biggest = max(range(1, 11), key=lambda i: (v["curve"][i - 1] - v["curve"][i]) - (v["curve_median"][i - 1] - v["curve_median"][i]))
            out.append(E(source=c.tool, kind="series", label=f"Retention curve: {t}",
                         value=f"{v['curve'][1]}% still watching at 10% of video",
                         compare=f"median video {v['curve_median'][1]}%; biggest extra drop at {(biggest - 1) * 10}-{biggest * 10}%",
                         data=[{"x": f"{i * 10}%", "this": v["curve"][i], "median": v["curve_median"][i]} for i in range(11)]))
    else:
        ch = d.CHANNEL
        rows = {
            "views": ("Views, last 28 days", f"{ch['views_28d']:,}", f"previous 28 days {ch['views_prev_28d']:,} ({_pct_change(ch['views_28d'], ch['views_prev_28d'])})"),
            "retention": ("Avg retention, last 28 days", f"{ch['retention_28d']}%", f"previous {ch['retention_prev_28d']}%"),
            "ctr": ("CTR, last 28 days", f"{ch['ctr_28d']}%", f"previous {ch['ctr_prev_28d']}%"),
            "subs": ("Subscribers", f"{ch['subs']:,}", f"{ch['subs_change_28d']} in 28 days"),
            "watch_time": ("Watch time, last 28 days", f"{ch['watch_time_28d_h']:,} h", f"previous {ch['watch_time_prev_28d_h']:,} h"),
            "avg_view_duration": ("Avg view duration, recent videos", d.VIDEOS[0]["avd"], f"median {d.VIDEOS[0]['avd_median']}"),
        }
        out = [E(source=c.tool, kind="stat", label=l, value=v_, compare=cmp) for m in c.metrics
               for (l, v_, cmp) in [rows[m]]]
        out.append(E(source=c.tool, kind="items", label="Views per recent video",
                     data=[f"{x['title']}: {x['views']:,} views, {x['retention']}% retention" for x in d.VIDEOS]))
    return out


def content_memory(c: s.ContentMemoryCall) -> list[E]:
    _latency()
    v = _video(c.video_ref) or d.VIDEOS[0]
    out = [E(source=c.tool, kind="text", label=f"Intro transcript: {v['title']}",
             value=f"intro lasts {v['intro_seconds']} s before the main topic starts", data=[v["intro_text"]])]
    if c.focus in ("topics", "structure", "full"):
        out.append(E(source=c.tool, kind="items", label=f"Topics covered: {v['title']}", data=v["topics"]))
    best = min(d.VIDEOS, key=lambda x: x["intro_seconds"])
    if best is not v:
        out.append(E(source=c.tool, kind="text", label=f"Intro of best video: {best['title']}",
                     value=f"intro {best['intro_seconds']} s, retention {best['retention']}%", data=[best["intro_text"]]))
    return out


def comment_agent(c: s.CommentAgentCall) -> list[E]:
    _latency()
    vids = d.VIDEOS if c.video_ref == "channel" else [_video(c.video_ref) or d.VIDEOS[0]]
    rows = [(t, likes, theme, v["title"]) for v in vids for (t, likes, theme) in v["comments"]]
    if c.sentiment == "negative":
        rows = [r for r in rows if r[2] not in ("praise",)]
    elif c.sentiment == "positive":
        rows = [r for r in rows if r[2] == "praise"]
    elif c.sentiment == "questions":
        rows = [r for r in rows if r[2] in ("question", "request")]
    if c.topic:
        hit = [r for r in rows if c.topic.lower() in r[0].lower() or c.topic.lower() in r[2]]
        rows = hit or rows
    rows.sort(key=lambda r: -r[1])
    themes: dict[str, int] = {}
    for r in rows:
        themes[r[2]] = themes.get(r[2], 0) + r[1]
    return [
        E(source=c.tool, kind="quotes", label="Top comments" + ("" if len(vids) > 1 else f": {vids[0]['title']}"),
          value=f"{len(rows)} matching comments", data=[f"\"{t}\" ({likes} likes)" for t, likes, _, _ in rows[:5]]),
        E(source=c.tool, kind="items", label="Comment themes (by likes)",
          data=[f"{k}: {n} likes" for k, n in sorted(themes.items(), key=lambda x: -x[1])]),
    ]


def rival_facts(c: s.RivalFactsCall) -> list[E]:
    _latency()
    names = list(d.RIVALS) if c.rival.lower() == "all" else [n for n in d.RIVALS if n.lower() in c.rival.lower() or c.rival.lower() in n.lower()]
    names = names or list(d.RIVALS)
    out = [E(source=c.tool, kind="stat", label="You", value=f"{d.CHANNEL['views_28d']:,} views (28d)",
             compare=f"{d.CHANNEL['subs']:,} subs, {len(d.VIDEOS)} uploads")]
    for n in names:
        r = d.RIVALS[n]
        out.append(E(source=c.tool, kind="stat", label=n, value=f"{r['views_28d']:,} views (28d)",
                     compare=f"{r['subs']:,} subs, {r['uploads_28d']} uploads"))
        out.append(E(source=c.tool, kind="items", label=f"{n}: top videos", data=r["top"]))
    return out


def expert_search(c: s.ExpertSearchCall) -> list[E]:
    _latency()
    words = set(c.query.lower().split())
    hits = sorted(d.EXPERT_TIPS, key=lambda t: -len(words & set(t[0].split())))[:3]
    return [E(source=c.tool, kind="items", label=f"Expert tips: {c.query}",
              data=[f"{tip} ({src})" for _, tip, src in hits])]


def trend_scout_saved(c: s.TrendScoutCall) -> list[E]:
    _latency()
    return [E(source=c.tool, kind="items", label="Trends in your niche (nightly scan)",
              data=[f"{t}: {why}" for t, why in d.TRENDS_SAVED])]


def voice_agent(c) -> list[E]:
    _latency()
    return [E(source=c.tool, kind="text", label="Your voice", value=d.VOICE)]


def niche_agent(c) -> list[E]:
    _latency()
    return [E(source=c.tool, kind="text", label="Your niche", value=d.NICHE)]


def post_doctor(c: s.PostDoctorCall) -> list[E]:
    _latency()
    v = _video(c.video_ref) or d.VIDEOS[0]
    return [E(source=c.tool, kind="text", label=f"Post Doctor: {v['title']}", value=v["doctor"])]


def growth_coach(c) -> list[E]:
    _latency()
    return [E(source=c.tool, kind="items", label="Growth plan (this week)", data=d.GROWTH_PLAN)]


def idea_agent(c: s.IdeaAgentCall) -> list[E]:
    _latency()
    return [E(source=c.tool, kind="items", label="Saved content ideas",
              data=[f"{t}: {why}" for t, why in d.IDEAS[:c.count]])]


TOOLS = {
    "channel_facts": channel_facts, "content_memory": content_memory, "comment_agent": comment_agent,
    "rival_facts": rival_facts, "expert_search": expert_search, "trend_scout": trend_scout_saved,
    "voice_agent": voice_agent, "niche_agent": niche_agent, "post_doctor": post_doctor,
    "growth_coach": growth_coach, "idea_agent": idea_agent,
    "account_facts": lambda c: [E(source=c.tool, kind="text", label="Instagram", value="No Instagram data in this POC")],
}
