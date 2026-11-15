"""/api/chat 对话模式：用户说一句话 → 回复 + 该怎么做（action）。真实 Gemini 版 chat() 后面加。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app import gemini
from app.steps import HistoryItem

ACTIONS = ("start", "resume", "advance", "stop", "clarify")
Action = Literal["start", "resume", "advance", "stop", "clarify"]
MAX_OUTLINE = 8


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class CurrentStep(BaseModel):
    instruction: str
    target_label: str | None = None
    box: list[int] | None = None
    expect: str | None = None


class TaskState(BaseModel):
    app: str
    goal: str
    outline: list[str] | None = None
    history: list[HistoryItem] = []
    current: CurrentStep | None = None


class ChatIn(BaseModel):
    lang: Literal["zh", "en"] = "zh"
    message: str
    screenshot: str
    messages: list[ChatMessage] = []
    task: TaskState | None = None


class ChatOut(BaseModel):
    reply: str
    action: Action
    app: str | None
    goal: str | None
    outline: list[str] | None
    note: str | None


class ChatRaw(BaseModel):
    """模型的原始输出：action 用 str，坏值由 finalize_chat 降级。"""

    reply: str
    action: str
    app: str | None = None
    goal: str | None = None
    outline: list[str] | None = None
    note: str | None = None


_FALLBACK_REPLY = {
    "zh": {"clarify": "你想在哪个软件里做什么？说具体一点我就能带你做。", "other": "好的。"},
    "en": {"clarify": "Which app are you using, and what do you want to do?", "other": "Okay."},
}


def _clean(s: str | None) -> str | None:
    s = (s or "").strip()
    return s or None


def finalize_chat(raw: "ChatRaw | ChatOut", has_task: bool, lang: str) -> ChatOut:
    """保证传给客户端的数据合法：action 一定在 5 个里，字段按 action 清理。"""
    action = (raw.action or "").strip().lower()
    if action not in ACTIONS:
        action = "resume" if has_task else "clarify"
    if action in ("advance", "stop") and not has_task:
        action = "clarify"
    app, goal = _clean(raw.app), _clean(raw.goal)
    if action == "start" and not goal:
        action = "clarify"
    outline = None
    if action == "start":
        outline = [x.strip() for x in (raw.outline or []) if x and x.strip()][:MAX_OUTLINE] or None
    else:
        app = goal = None
    note = _clean(raw.note) if action == "advance" else None
    texts = _FALLBACK_REPLY.get(lang, _FALLBACK_REPLY["zh"])
    reply = _clean(raw.reply) or (texts["clarify"] if action == "clarify" else texts["other"])
    return ChatOut(reply=reply, action=action, app=app, goal=goal, outline=outline, note=note)


_MOCK_OUTLINE = {
    "zh": ["[MOCK] 第一步", "[MOCK] 第二步", "[MOCK] 第三步"],
    "en": ["[MOCK] Step one", "[MOCK] Step two", "[MOCK] Step three"],
}
_ADVANCE_WORDS = ("跳过", "做完", "好了", "skip", "done")
_STOP_WORDS = ("停", "stop")


def mock_chat(body: ChatIn) -> ChatOut:
    msg = body.message.strip()
    low = msg.lower()
    en = body.lang == "en"
    if body.task is None:
        return ChatOut(
            reply=f"(mock) Okay, let's do it: {msg}" if en else f"（mock）好的，我们开始：{msg}",
            action="start", app="Numbers", goal=msg, outline=_MOCK_OUTLINE[body.lang], note=None,
        )
    if any(w in low for w in _ADVANCE_WORDS):
        return ChatOut(reply="(mock) Got it, next step." if en else "（mock）好的，看下一步。",
                       action="advance", app=None, goal=None, outline=None, note=msg)
    if any(w in low for w in _STOP_WORDS):
        return ChatOut(reply="(mock) Okay, stopping here." if en else "（mock）好的，先停在这里。",
                       action="stop", app=None, goal=None, outline=None, note=None)
    return ChatOut(reply=f"(mock) You asked: {msg}" if en else f"（mock）你问的是：{msg}",
                   action="resume", app=None, goal=None, outline=None, note=None)


def chat(body: ChatIn) -> ChatOut:
    """真实 Gemini 版（下一步实现）。暂时用 mock。"""
    return mock_chat(body)
