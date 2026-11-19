"""Step 1: the catalog the planner LLM reads.

Each entry: name, when to use it, input schema (from schemas.py), what it returns.
The planner prompt is built from this, so there is one source of truth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel

import schemas as s


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str            # "tool" | "reader" | "saved_answer"
    platform: str        # "youtube" | "instagram" | "both"
    use_when: str
    returns: str
    args: type[BaseModel]


CATALOG: dict[str, ToolSpec] = {t.name: t for t in [
    # --- tools: numbers from the DB ---
    ToolSpec("channel_facts", "tool", "youtube",
             "YouTube numbers: views, retention, CTR, subs, watch time, for the channel or one video.",
             "metric values, change vs channel median, retention drop points", s.ChannelFactsCall),
    ToolSpec("account_facts", "tool", "instagram",
             "Instagram numbers: reach, plays, saves, shares, followers, for the account or one reel.",
             "metric values, change vs account median", s.AccountFactsCall),
    ToolSpec("rival_facts", "tool", "both",
             "Questions comparing the creator with tracked rivals.",
             "rival metrics side by side, their top posts", s.RivalFactsCall),
    ToolSpec("expert_search", "tool", "both",
             "General how-to or best-practice advice (hooks, thumbnails, posting time).",
             "up to 5 expert tips with source names", s.ExpertSearchCall),
    # --- readers: saved data, no LLM ---
    ToolSpec("content_memory", "reader", "both",
             "What a video/reel says or how it is structured (intro, topics). Use with retention questions.",
             "transcript snippets with timestamps, topic list", s.ContentMemoryCall),
    ToolSpec("comment_agent", "reader", "both",
             "What viewers say: complaints, questions, praise.",
             "top comments grouped by theme, with counts", s.CommentAgentCall),
    ToolSpec("trend_scout", "reader", "both",
             'Trends in the niche. Use mode "saved" by default; "live" only if user asks for today/latest.',
             "trending topics with a short why", s.TrendScoutCall),
    ToolSpec("voice_agent", "reader", "both",
             "The creator's style/tone, when advice must match how they talk.",
             "saved voice profile summary", s.VoiceAgentCall),
    ToolSpec("niche_agent", "reader", "both",
             "Which niche/audience the creator is in.",
             "niche label, audience summary", s.NicheAgentCall),
    # --- saved writer answers from nightly jobs ---
    ToolSpec("post_doctor", "saved_answer", "both",
             "A saved diagnosis of one post: what worked, what did not.",
             "saved diagnosis text and scores", s.PostDoctorCall),
    ToolSpec("growth_coach", "saved_answer", "both",
             "Overall growth plan / 'how do I grow'.",
             "saved weekly growth plan", s.GrowthCoachCall),
    ToolSpec("idea_agent", "saved_answer", "both",
             "Content ideas: 'what should I post next'.",
             "saved idea list with reasons", s.IdeaAgentCall),
]}


# Decisions that need no tools. Described exactly like tools: when to use + inputs.
# The planner writes the reply itself, so these cost just 1 LLM call.
DECISIONS = [
    ("answer", "The message is about the creator's channel, videos, reels, audience, growth, rivals or trends.",
     "calls: tools from TOOLS. Leave reply empty."),
    ("small_talk", "Greetings, thanks, chit-chat: hi, hello, hey, thanks, good morning, namaste, how are you.",
     "reply: 1-2 warm, natural sentences greeting back and saying you help YouTube/Instagram creators with "
     "analytics, comments, competitors, trends and content ideas. No calls."),
    ("clarify", "Too vague to pick tools: help, why?, what should I do, it's not working.",
     "reply: one short friendly question asking what they mean. clarify_options: up to 3. No calls."),
    ("out_of_scope", "Not about growing on YouTube/Instagram: weather, news, coding, homework, politics, health.",
     "reply: kindly say it's not your area in 1-2 sentences, then offer help with their channel. No calls."),
    ("hand_off", "The user wants Contiq to CREATE something: write a script, titles, description, captions, "
     "hashtags, thumbnail, clips, media kit, content ideas.",
     "hand_off: one of yt_script, ig_script, yt_titles, yt_description_tags, ig_captions_hashtags, content_ideas, "
     "thumbnail, clips, media_kit, comment_inbox, next_list, video_page, reel_page. "
     "reply: one sentence saying you'll open it. No calls."),
]


def planner_catalog(platform: str) -> str:
    """Compact text for the planner prompt: decisions first, then the tools for this platform."""
    lines = ["DECISIONS (pick one):"]
    for name, use_when, inputs in DECISIONS:
        lines.append(f"- {name}\n  use when: {use_when}\n  inputs: {inputs}")
    lines.append("\nTOOLS (only for decision \"answer\"):")
    for t in CATALOG.values():
        if t.platform not in (platform, "both"):
            continue
        schema = t.args.model_json_schema()
        inputs = {k: v.get("enum") or v.get("items", {}).get("enum") or v.get("type", "str")
                  for k, v in schema.get("properties", {}).items() if k != "tool"}
        lines.append(f"- {t.name} [{t.kind}]\n  use when: {t.use_when}\n"
                     f"  inputs: {json.dumps(inputs)}\n  returns: {t.returns}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"{len(CATALOG)} tools in catalog\n")
    print(planner_catalog("youtube"))
