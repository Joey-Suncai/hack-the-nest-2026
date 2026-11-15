from app import chat
from app.chat import ChatIn, ChatRaw, finalize_chat, mock_chat
from conftest import TINY_PNG_B64

TASK = {"app": "Numbers", "goal": "做柱状图", "outline": None, "history": [],
        "current": {"instruction": "点图表", "target_label": "图表", "box": [1, 2, 3, 4], "expect": "出现菜单"}}


def body(message, task=None, lang="zh"):
    return ChatIn(lang=lang, message=message, screenshot=TINY_PNG_B64, task=task)


def raw(**kw):
    base = dict(reply="好", action="resume", app=None, goal=None, outline=None, note=None)
    base.update(kw)
    return ChatRaw(**base)


def test_mock_start_without_task():
    out = mock_chat(body("我想做柱状图"))
    assert out.action == "start" and out.app == "Numbers" and out.goal == "我想做柱状图"
    assert len(out.outline) == 3


def test_mock_keywords_with_task():
    assert mock_chat(body("这步做完了", TASK)).action == "advance"
    assert mock_chat(body("这步做完了", TASK)).note == "这步做完了"
    assert mock_chat(body("skip this", TASK, "en")).action == "advance"
    assert mock_chat(body("停一下", TASK)).action == "stop"
    out = mock_chat(body("这个按钮是干嘛的", TASK))
    assert out.action == "resume" and out.reply == "（mock）你问的是：这个按钮是干嘛的"


def test_mock_english_reply():
    out = mock_chat(body("what is this", TASK, "en"))
    assert out.action == "resume" and "what is this" in out.reply and "mock" in out.reply.lower()
    assert all(ord(ch) < 0x3000 for ch in out.reply)


def test_finalize_bad_action_degrades():
    assert finalize_chat(raw(action="jump"), has_task=True, lang="zh").action == "resume"
    assert finalize_chat(raw(action="jump"), has_task=False, lang="zh").action == "clarify"


def test_finalize_start_without_goal_is_clarify():
    out = finalize_chat(raw(action="start", app="Numbers", goal="  "), has_task=False, lang="zh")
    assert out.action == "clarify" and out.app is None and out.goal is None


def test_finalize_clears_fields_by_action():
    out = finalize_chat(raw(action="resume", app="X", goal="Y", outline=["a"], note="n"), has_task=True, lang="zh")
    assert (out.app, out.goal, out.outline, out.note) == (None, None, None, None)
    out = finalize_chat(raw(action="advance", app="X", note="点错了"), has_task=True, lang="zh")
    assert out.app is None and out.note == "点错了"
    out = finalize_chat(raw(action="start", app="Numbers", goal="做图", outline=["", " a ", "b"] + ["c"] * 10),
                        has_task=False, lang="zh")
    assert out.outline == ["a", "b", "c", "c", "c", "c", "c", "c"]


def test_finalize_progress_actions_without_task_are_clarify():
    assert finalize_chat(raw(action="advance"), has_task=False, lang="zh").action == "clarify"
    assert finalize_chat(raw(action="stop"), has_task=False, lang="zh").action == "clarify"


def test_finalize_empty_reply_gets_fallback():
    assert finalize_chat(raw(reply=" ", action="resume"), has_task=True, lang="en").reply


def test_endpoint_validation(client):
    r = client.post("/api/chat", json={"lang": "zh", "message": " ", "screenshot": TINY_PNG_B64})
    assert r.status_code == 400 and r.json() == {"error": "message 不能为空"}
    r = client.post("/api/chat", json={"lang": "zh", "message": "hi", "screenshot": "bad"})
    assert r.status_code == 400 and r.json() == {"error": "screenshot 不是有效的图片"}


def test_endpoint_mock(client, monkeypatch):
    monkeypatch.setattr(chat.gemini, "MOCK", True)
    r = client.post("/api/chat", json={"lang": "zh", "message": "做柱状图", "screenshot": TINY_PNG_B64,
                                       "messages": [], "task": None})
    assert r.status_code == 200
    j = r.json()
    assert j["action"] == "start" and j["goal"] == "做柱状图" and set(j) == {"reply", "action", "app", "goal", "outline", "note"}
    r = client.post("/api/chat", json={"lang": "zh", "message": "做完了", "screenshot": TINY_PNG_B64, "task": TASK})
    assert r.json()["action"] == "advance"
