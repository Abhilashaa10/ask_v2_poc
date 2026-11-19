"""Step 1: data formats for Ask v2 POC.

- Tool inputs  : one small Pydantic model per tool (the planner fills these)
- AskPlan      : what the planner LLM returns (LLM call 1)
- AskAnswer    : what the answer LLM returns (LLM call 2)
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

# ---------- shared small types ----------

Period = Literal["7d", "28d", "90d"]

# The planner never writes raw IDs. It says "latest", "channel", or "1".."10"
# (position in the recent-videos list we show it). Code resolves the real ID.
VideoRef = Annotated[str, Field(description='"latest", "channel", or "1".."10" from the recent list')]


def _check_ref(v: str) -> str:
    if v in ("latest", "channel") or (v.isdigit() and 1 <= int(v) <= 10):
        return v
    raise ValueError(f"bad video_ref: {v!r}")


# ---------- tool inputs (one class per tool) ----------

class ChannelFactsCall(BaseModel):
    tool: Literal["channel_facts"] = "channel_facts"
    metrics: list[Literal["views", "retention", "ctr", "subs", "watch_time", "avg_view_duration"]] = Field(min_length=1, max_length=4)
    period: Period = "28d"
    video_ref: VideoRef = "channel"
    _v = field_validator("video_ref")(classmethod(lambda cls, v: _check_ref(v)))


class AccountFactsCall(BaseModel):
    tool: Literal["account_facts"] = "account_facts"
    metrics: list[Literal["reach", "plays", "saves", "shares", "followers"]] = Field(min_length=1, max_length=4)
    period: Period = "28d"
    video_ref: VideoRef = "channel"
    _v = field_validator("video_ref")(classmethod(lambda cls, v: _check_ref(v)))


class RivalFactsCall(BaseModel):
    tool: Literal["rival_facts"] = "rival_facts"
    rival: str = Field(description='a tracked rival name, or "all"')
    metric: Literal["views", "uploads", "subs", "top_videos"] = "views"
    period: Period = "28d"


class ExpertSearchCall(BaseModel):
    tool: Literal["expert_search"] = "expert_search"
    query: str = Field(max_length=120)


class ContentMemoryCall(BaseModel):
    tool: Literal["content_memory"] = "content_memory"
    video_ref: VideoRef = "latest"
    focus: Literal["intro", "structure", "topics", "full"] = "full"
    _v = field_validator("video_ref")(classmethod(lambda cls, v: _check_ref(v)))


class CommentAgentCall(BaseModel):
    tool: Literal["comment_agent"] = "comment_agent"
    video_ref: VideoRef = "latest"
    topic: str = Field(default="", max_length=60, description="optional keyword to search comments for")
    sentiment: Literal["any", "negative", "positive", "questions"] = "any"
    _v = field_validator("video_ref")(classmethod(lambda cls, v: _check_ref(v)))


class TrendScoutCall(BaseModel):
    tool: Literal["trend_scout"] = "trend_scout"
    mode: Literal["saved", "live"] = Field("saved", description='"live" = web search, costs the 3rd LLM call')
    topic: str = Field(default="", max_length=60)


class VoiceAgentCall(BaseModel):
    tool: Literal["voice_agent"] = "voice_agent"


class NicheAgentCall(BaseModel):
    tool: Literal["niche_agent"] = "niche_agent"


class PostDoctorCall(BaseModel):
    tool: Literal["post_doctor"] = "post_doctor"
    video_ref: VideoRef = "latest"
    _v = field_validator("video_ref")(classmethod(lambda cls, v: _check_ref(v)))


class GrowthCoachCall(BaseModel):
    tool: Literal["growth_coach"] = "growth_coach"


class IdeaAgentCall(BaseModel):
    tool: Literal["idea_agent"] = "idea_agent"
    count: int = Field(3, ge=1, le=5)


ToolCall = Annotated[
    Union[
        ChannelFactsCall, AccountFactsCall, RivalFactsCall, ExpertSearchCall,
        ContentMemoryCall, CommentAgentCall, TrendScoutCall, VoiceAgentCall,
        NicheAgentCall, PostDoctorCall, GrowthCoachCall, IdeaAgentCall,
    ],
    Field(discriminator="tool"),
]


# ---------- LLM call 1: planner output ----------

class AskPlan(BaseModel):
    decision: Literal["answer", "small_talk", "clarify", "out_of_scope", "hand_off", "error"]
    language: Literal["en", "hi", "hinglish"] = "en"
    answer_tier: Literal["default", "premium"] = "default"
    calls: list[ToolCall] = Field(default_factory=list, max_length=6)
    clarify_options: list[str] = Field(default_factory=list, max_length=3)
    hand_off: Optional[Feature] = Field(None, description="feature to open when decision is hand_off")
    reply: str = Field(default="", max_length=400, description="planner's own reply when no tools are needed")
    reason: str = Field(default="", max_length=200, description="one line, for logs only")

    @property
    def needs_web(self) -> bool:
        return any(c.tool == "trend_scout" and c.mode == "live" for c in self.calls)


# ---------- LLM call 2: answer output ----------

class Feature(str, Enum):
    """Whitelist: a button can only open one of these."""
    video_page = "video_page"
    reel_page = "reel_page"
    yt_titles = "yt_titles"
    yt_description_tags = "yt_description_tags"
    ig_captions_hashtags = "ig_captions_hashtags"
    content_ideas = "content_ideas"
    ig_script = "ig_script"
    yt_script = "yt_script"
    clips = "clips"
    media_kit = "media_kit"
    thumbnail = "thumbnail"
    comment_inbox = "comment_inbox"
    next_list = "next_list"


class KV(BaseModel):
    key: str
    value: str


class Block(BaseModel):
    type: Literal["stat", "chart", "list", "quote"]
    title: str = Field(max_length=60)
    evidence_ids: list[str] = Field(min_length=1, description="code draws the block from these")


class ActionItem(BaseModel):
    text: str = Field(max_length=160)
    evidence_ids: list[str] = Field(default_factory=list)


class Cta(BaseModel):
    feature: Feature
    label: str = Field(max_length=40)
    prefill: list[KV] = Field(default_factory=list, max_length=5)


class AskAnswer(BaseModel):
    summary: str = Field(description="2-4 short sentences, plain language")
    blocks: list[Block] = Field(default_factory=list, max_length=4)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=3)
    ctas: list[Cta] = Field(default_factory=list, max_length=2)
    follow_ups: list[str] = Field(default_factory=list, max_length=3)
    sources: list[str] = Field(default_factory=list)


# ---------- what tools return (step 3) ----------

class Evidence(BaseModel):
    """One finding. The answer LLM may only use numbers from these, and cites them by id."""
    id: str = ""                       # set by the runner: e1, e2, ...
    source: str                        # tool name
    kind: Literal["stat", "series", "quotes", "items", "text"]
    label: str
    value: str = ""                    # the headline number or text
    compare: str = ""                  # e.g. "channel median 44%"
    as_of: str = ""
    data: list[Any] = Field(default_factory=list)   # chart points, comments, list items


AskPlan.model_rebuild()


# ---------- quick self-test: python schemas.py ----------
if __name__ == "__main__":
    plan = AskPlan.model_validate({
        "decision": "answer",
        "answer_tier": "premium",
        "calls": [
            {"tool": "channel_facts", "metrics": ["retention", "avg_view_duration"], "video_ref": "latest"},
            {"tool": "content_memory", "video_ref": "latest", "focus": "intro"},
            {"tool": "comment_agent", "video_ref": "latest", "sentiment": "negative"},
        ],
        "reason": "retention drop on latest video",
    })
    print("plan ok:", [c.tool for c in plan.calls], "| needs_web:", plan.needs_web)

    ans = AskAnswer.model_validate({
        "summary": "Viewers leave in the first 30 seconds.",
        "blocks": [{"type": "chart", "title": "Retention curve", "evidence_ids": ["e1"]}],
        "action_items": [{"text": "Cut the intro to under 10 seconds", "evidence_ids": ["e1"]}],
        "ctas": [{"feature": "yt_script", "label": "Rewrite intro",
                  "prefill": [{"key": "video_ref", "value": "latest"}, {"key": "section", "value": "intro"}]}],
        "follow_ups": ["Compare with my last 5 videos"],
    })
    print("answer ok:", ans.ctas[0].feature.value)

    for bad in ({"decision": "answer", "calls": [{"tool": "delete_channel"}]},
                {"decision": "answer", "calls": [{"tool": "content_memory", "video_ref": "abc123"}]}):
        try:
            AskPlan.model_validate(bad)
        except Exception as e:
            print("rejected ok:", type(e).__name__)
