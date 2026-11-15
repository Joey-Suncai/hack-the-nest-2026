from app import steps
from app.steps import HistoryItem, NextStepIn, NextStepOut, finalize, mock_next_step, normalize_box
from conftest import TINY_PNG_B64


def test_normalize_box_swaps_and_clamps():
    assert normalize_box([600, 100, 500, 1200]) == [500, 100, 600, 1000]


def test_normalize_box_rejects_bad():
    assert normalize_box([1, 2, 3]) is None
    assert normalize_box(None) is None
    assert normalize_box([10, 10, 10, 50]) is None  # 高度为 0
    assert normalize_box(["a", 1, 2, 3]) is None


def test_normalize_box_rounds_floats():
    assert normalize_box([10.4, 20.6, 30, 40]) == [10, 21, 30, 40]


def test_next_without_box_becomes_unsure():
    out = finalize(NextStepOut(status="next", previous_ok=None, instruction="点这里",
                               target_label=None, box=None, expect=None))
    assert out.status == "unsure"


def test_done_drops_box():
    out = finalize(NextStepOut(status="done", previous_ok=True, instruction="完成",
                               target_label=None, box=[1, 2, 3, 4], expect=None))
    assert out.box is None


def test_mock_sequence():
    body = NextStepIn(app="Numbers", goal="做柱状图", screenshot=TINY_PNG_B64)
    first = mock_next_step(body)
    assert first.status == "next" and first.box == [450, 450, 550, 550] and first.previous_ok is None
    body.history = [HistoryItem(instruction="x", ok=True)] * 3
    assert mock_next_step(body).status == "done"


def test_endpoint_rejects_bad_screenshot(client):
    r = client.post("/api/next-step", json={"app": "a", "goal": "g", "screenshot": "notbase64"})
    assert r.status_code == 400 and r.json() == {"error": "screenshot 不是有效的图片"}


def test_endpoint_rejects_empty_goal(client):
    r = client.post("/api/next-step", json={"app": "a", "goal": " ", "screenshot": TINY_PNG_B64})
    assert r.status_code == 400 and r.json() == {"error": "goal 不能为空"}


def test_endpoint_mock_ok(client, monkeypatch):
    monkeypatch.setattr(steps.gemini, "MOCK", True)
    r = client.post("/api/next-step", json={"app": "a", "goal": "g", "screenshot": TINY_PNG_B64,
                                            "history": [{"instruction": "x", "ok": True}]})
    assert r.status_code == 200
    assert r.json()["status"] == "next" and r.json()["box"] == [300, 600, 380, 720]


def test_next_step_finalizes_model_output(monkeypatch):
    monkeypatch.setattr(steps.gemini, "generate_json", lambda *a, **k: NextStepOut(
        status="next", previous_ok=True, instruction="点图表", target_label="图表",
        box=[600, 100, 500, 1200], expect="出现菜单"))
    out = steps.next_step(NextStepIn(app="Numbers", goal="做柱状图", screenshot=TINY_PNG_B64))
    assert out.box == [500, 100, 600, 1000]
    assert out.previous_ok is None  # 第一步没有上一步


def test_build_prompt_fills_placeholders():
    body = NextStepIn(app="Numbers", goal="做柱状图", lang="en", screenshot=TINY_PNG_B64,
                      history=[HistoryItem(instruction="点图表", ok=True)], expect="出现菜单",
                      guide=["插入 > 图表"])
    p = steps.build_prompt(body)
    assert "Numbers" in p and "做柱状图" in p and "English" in p
    assert "1. 点图表（完成）" in p and "出现菜单" in p and "- 插入 > 图表" in p
    assert "{" not in p


def test_finalize_english_fallback():
    out = finalize(NextStepOut(status="next", previous_ok=None, instruction="", target_label=None,
                               box=None, expect=None), "en")
    assert out.status == "unsure" and out.instruction.startswith("I can't tell")


def test_build_prompt_includes_user_note():
    body = NextStepIn(app="Numbers", goal="做柱状图", screenshot=TINY_PNG_B64, user_note="用户说点错了")
    assert "用户说点错了" in steps.build_prompt(body)
    assert "（无）" in steps.build_prompt(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64))


def test_build_prompt_includes_frontmost_app():
    body = NextStepIn(app="Numbers", goal="g", screenshot=TINY_PNG_B64, frontmost_app="Safari")
    p = steps.build_prompt(body)
    assert "Safari" in p and "{" not in p
