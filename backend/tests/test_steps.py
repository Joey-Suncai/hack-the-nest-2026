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


def _out(**kw):
    base = dict(status="next", previous_ok=None, instruction="点图表", target_label="图表",
                box=[10, 10, 50, 50], expect="出现菜单")
    base.update(kw)
    return NextStepOut(**base)


def test_finalize_keeps_valid_next_guess():
    g = steps.NextGuess(label=" 二维 ", instruction=" Click 2D. ", expect="styles appear")
    out = finalize(_out(next_guess=g))
    assert out.next_guess.label == "二维" and out.next_guess.instruction == "Click 2D."


def test_finalize_drops_bad_next_guess():
    assert finalize(_out(next_guess=steps.NextGuess(label=" ", instruction="x"))).next_guess is None
    assert finalize(_out(status="done", box=None, next_guess=steps.NextGuess(label="a", instruction="b"))).next_guess is None
    assert finalize(_out(box=None, next_guess=steps.NextGuess(label="a", instruction="b"))).next_guess is None  # → unsure


def test_endpoint_returns_next_guess_key(client, monkeypatch):
    monkeypatch.setattr(steps.gemini, "MOCK", True)
    r = client.post("/api/next-step", json={"app": "a", "goal": "g", "screenshot": TINY_PNG_B64})
    assert "next_guess" in r.json()


# ---------- elements（macOS 辅助功能控件）----------
ELEMENTS = [
    {"id": 1, "role": "AXStaticText", "name": None, "box": [10, 10, 20, 20]},
    {"id": 12, "role": "AXButton", "name": "New Tab", "box": [40, 950, 62, 966]},
    {"id": 13, "role": "AXTextField", "name": None, "box": [120, 300, 140, 700]},
]


def _els():
    return [steps.Element(**e) for e in ELEMENTS]


def test_request_parses_with_and_without_elements():
    assert NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64).elements is None
    assert NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, elements=None).elements is None
    body = NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, elements=ELEMENTS)
    assert body.elements[1].name == "New Tab" and body.elements[0].name is None
    assert body.elements[2].box == [120, 300, 140, 700]


def test_known_element_id_overrides_box_and_label():
    out = finalize(_out(box=[500, 500, 520, 520], target_label="new tab", element_id=12), "en", _els())
    assert out.element_id == 12 and out.box == [40, 950, 62, 966] and out.target_label == "New Tab"


def test_unnamed_element_keeps_model_label():
    out = finalize(_out(target_label="title box", element_id=13), "en", _els())
    assert out.element_id == 13 and out.box == [120, 300, 140, 700] and out.target_label == "title box"


def test_unknown_element_id_falls_back_to_model_box():
    out = finalize(_out(box=[10, 10, 50, 50], element_id=99), "en", _els())
    assert out.element_id is None and out.box == [10, 10, 50, 50] and out.target_label == "图表"
    assert finalize(_out(element_id=12)).element_id is None  # 请求没有 elements


def test_element_id_dropped_when_not_next():
    out = finalize(_out(status="done", box=None, element_id=12), "en", _els())
    assert out.element_id is None and out.box is None


def test_prompt_without_elements_unchanged():
    kw = dict(app="Numbers", goal="做柱状图", lang="en", screenshot=TINY_PNG_B64,
              history=[HistoryItem(instruction="点图表", ok=True)], expect="出现菜单", guide=["插入 > 图表"])
    base = steps.gemini.load_prompt(
        "next_step", app="Numbers", goal="做柱状图", lang_name="English", history="1. 点图表（完成）",
        expect="出现菜单", guide="- 插入 > 图表", user_note="（无）", frontmost_app="（未知）")
    assert steps.build_prompt(NextStepIn(**kw)) == base
    assert steps.build_prompt(NextStepIn(**kw, elements=[])) == base
    p = steps.build_prompt(NextStepIn(**kw, elements=ELEMENTS))
    assert p.startswith(base) and '#12 AXButton "New Tab" [40,950,62,966]' in p
    assert "#13 AXTextField null [120,300,140,700]" in p and "{" not in p


def test_next_step_uses_element_and_skips_refine(monkeypatch):
    calls = []

    def fake(prompt, schema, **k):
        calls.append(schema)
        return NextStepOut(status="next", previous_ok=None, instruction="Click New Tab.", target_label="+",
                           box=[45, 945, 60, 960], expect="a tab opens", element_id=12)

    monkeypatch.setattr(steps.gemini, "generate_json", fake)
    monkeypatch.setattr(steps, "REFINE", True)
    out = steps.next_step(NextStepIn(app="Chrome", goal="g", screenshot=TINY_PNG_B64, elements=ELEMENTS))
    assert out.element_id == 12 and out.box == [40, 950, 62, 966] and out.target_label == "New Tab"
    assert calls == [NextStepOut]  # 没有调精修


def test_mock_returns_first_named_element():
    body = NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, elements=ELEMENTS)
    out = mock_next_step(body)
    assert out.element_id == 12 and out.box == [40, 950, 62, 966] and out.target_label == "New Tab"


def test_endpoint_mock_with_elements(client, monkeypatch):
    monkeypatch.setattr(steps.gemini, "MOCK", True)
    body = {"app": "a", "goal": "g", "screenshot": TINY_PNG_B64, "elements": ELEMENTS}
    j = client.post("/api/next-step", json=body).json()
    assert j["element_id"] == 12 and j["box"] == [40, 950, 62, 966] and j["target_label"] == "New Tab"
    j = client.post("/api/next-step", json={**body, "elements": None}).json()
    assert j["element_id"] is None and j["box"] == [450, 450, 550, 550]
