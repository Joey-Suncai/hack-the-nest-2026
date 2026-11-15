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
        expect="出现菜单", guide="- 插入 > 图表", user_note="（无）", frontmost_app="（未知）", context="")
    assert steps.build_prompt(NextStepIn(**kw)) == base
    assert steps.build_prompt(NextStepIn(**kw, elements=[])) == base
    p = steps.build_prompt(NextStepIn(**kw, elements=ELEMENTS))
    assert p.startswith(base) and '- AXButton "New Tab" [40,950,62,966]' in p
    assert "- AXTextField null [120,300,140,700]" in p and "{" not in p and "#12" not in p  # 不给编号


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
    assert calls == [steps.NextStepRaw]  # 没有调精修（主调用用的是编号写成字符串的格式）


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


# ---------- 推理：situation / reasoning / plan / app，confirmed / app_switched_from / deep ----------
def test_request_parses_new_fields():
    body = NextStepIn(app="Zoom", goal="g", screenshot=TINY_PNG_B64, deep=True, app_switched_from="Zoom",
                      frontmost_app="Google Chrome",
                      confirmed={"kind": "click", "target": "开始时间", "detail": "focused AXComboBox '开始时间'"})
    assert body.deep is True and body.app_switched_from == "Zoom"
    assert body.confirmed.kind == "click" and body.confirmed.target == "开始时间"
    plain = NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64)
    assert plain.deep is False and plain.confirmed is None and plain.app_switched_from is None
    minimal = NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, confirmed={"kind": "enter"})
    assert minimal.confirmed.target is None and minimal.confirmed.detail is None


def test_bad_confirmed_is_dropped_not_rejected(client, monkeypatch):
    assert NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, confirmed={"kind": "drag"}).confirmed is None
    assert NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, confirmed="yes").confirmed is None
    monkeypatch.setattr(steps.gemini, "MOCK", True)
    r = client.post("/api/next-step", json={"app": "a", "goal": "g", "screenshot": TINY_PNG_B64,
                                            "confirmed": {"kind": "wave"}})
    assert r.status_code == 200


def test_finalize_sanitizes_reasoning_fields():
    out = finalize(_out(situation="  Chrome opened   sign-in.  ", reasoning=" ", app=" Google Chrome ",
                        plan=[" Click your account ", "", "  ", "x" * 200] + [f"step {i}" for i in range(10)]))
    assert out.situation == "Chrome opened sign-in." and out.reasoning is None and out.app == "Google Chrome"
    assert out.plan[0] == "Click your account" and out.plan[1] == "x" * 80
    assert len(out.plan) == 8 and all(p.strip() and len(p) <= 80 for p in out.plan)
    assert finalize(_out(plan=["", " "])).plan is None
    assert finalize(_out(app="")).app is None


def test_finalize_drops_plan_when_done():
    out = finalize(_out(status="done", box=None, plan=["a"], situation="all set"))
    assert out.plan is None and out.situation == "all set"


def test_new_fields_absent_by_default():
    out = finalize(_out())
    assert out.situation is None and out.reasoning is None and out.plan is None and out.app is None


def _hist(ok=True):
    return [HistoryItem(instruction="x", ok=ok)]


def test_thinking_choice(monkeypatch):
    monkeypatch.setattr(steps, "THINKING", "low")
    monkeypatch.setattr(steps, "DEEP_THINKING", "medium")
    base = dict(app="a", goal="g", screenshot=TINY_PNG_B64)
    assert steps.choose_thinking(NextStepIn(**base, history=_hist())) == ("low", "routine")
    assert steps.choose_thinking(NextStepIn(**base, history=_hist(None))) == ("low", "routine")
    assert steps.choose_thinking(NextStepIn(**base, history=_hist(),
                                            confirmed={"kind": "click"}))[0] == "low"  # 确认本身不加思考
    assert steps.choose_thinking(NextStepIn(**base, history=_hist(), deep=True)) == ("medium", "deep requested")
    assert steps.choose_thinking(NextStepIn(**base)) == ("medium", "first step")
    assert steps.choose_thinking(NextStepIn(**base, history=_hist(), user_note="点错了")) == ("medium", "user note")
    assert steps.choose_thinking(NextStepIn(**base, history=_hist(), user_note="  "))[0] == "low"
    assert steps.choose_thinking(NextStepIn(**base, history=_hist(), app_switched_from="Zoom")) == \
        ("medium", "app switched")
    assert steps.choose_thinking(NextStepIn(**base, history=_hist(False))) == ("medium", "previous step failed")


def test_next_step_passes_thinking(monkeypatch, capsys):
    seen = []

    def fake(prompt, schema, files=None, thinking=None):
        seen.append(thinking)
        return _out()

    monkeypatch.setattr(steps.gemini, "generate_json", fake)
    monkeypatch.setattr(steps, "THINKING", "low")
    monkeypatch.setattr(steps, "DEEP_THINKING", "medium")
    monkeypatch.setattr(steps, "REFINE_ON_RETRY", False)
    steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, history=_hist(),
                               app_switched_from="Zoom", frontmost_app="Google Chrome"))
    steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, history=_hist()))
    assert seen == ["medium", "low"]
    assert "[next-step] thinking=medium (reason: app switched)" in capsys.readouterr().out


def test_prompt_optional_sections_only_when_given():
    base = dict(app="Zoom", goal="schedule a meeting", screenshot=TINY_PNG_B64, history=_hist(),
                frontmost_app="Google Chrome")
    plain = steps.build_prompt(NextStepIn(**base))
    assert steps.context_section(NextStepIn(**base)) == ""
    assert "换成了「" not in plain and "按下面的规则 E 处理" not in plain and "{" not in plain
    p = steps.build_prompt(NextStepIn(**base, app_switched_from="Zoom"))
    assert "从「Zoom」换成了「Google Chrome」" in p and "按下面的规则 E 处理" not in p
    p = steps.build_prompt(NextStepIn(**base, confirmed={"kind": "click", "target": "开始时间",
                                                         "detail": "focused AXComboBox '开始时间'"}))
    assert "客户端已确认上一步：用户确实做了上一步（点击「开始时间」），之后辅助功能读到：focused AXComboBox '开始时间'。" in p
    assert "换成了「" not in p
    p = steps.build_prompt(NextStepIn(**base, guide=["Click your account", "Click Save"]))
    assert "- Click your account\n- Click Save" in p
    assert "- Click your account" not in plain
    assert "先想清楚，再回答" in plain  # 静态的推理规则总是在


def test_mock_fills_reasoning_fields(client, monkeypatch):
    monkeypatch.setattr(steps.gemini, "MOCK", True)
    j = client.post("/api/next-step", json={"app": "Zoom", "goal": "g", "screenshot": TINY_PNG_B64,
                                            "app_switched_from": "Zoom", "frontmost_app": "Google Chrome"}).json()
    assert j["status"] == "next" and j["app"] == "Google Chrome" and j["plan"] and j["situation"]
    done = client.post("/api/next-step", json={"app": "a", "goal": "g", "screenshot": TINY_PNG_B64,
                                               "history": [{"instruction": "x", "ok": True}] * 3}).json()
    assert done["status"] == "done" and done["plan"] is None


# ---------- check：这一步做完后的关键指标 ----------
def _chk(**kw):
    return steps.Check(**kw)


def test_check_parses_and_defaults_to_none():
    out = NextStepOut.model_validate({
        "status": "next", "previous_ok": None, "instruction": "x", "target_label": "y", "box": [1, 2, 3, 4],
        "expect": None, "check": {"kind": "value_contains", "element_id": 15, "label": "开始日期",
                                  "expect": "11/16", "box": None}})
    assert out.check.kind == "value_contains" and out.check.element_id == 15 and out.check.expect == "11/16"
    assert _out().check is None and finalize(_out()).check is None
    fields = list(NextStepOut.model_fields)
    assert fields.index("check") > fields.index("expect") > fields.index("instruction")


def test_check_unknown_kind_dropped():
    assert finalize(_out(check=_chk(kind="changed", label="New Tab"))).check is None
    assert finalize(_out(check=_chk(kind=" Appears ", expect="Following"))).check.kind == "appears"


def test_check_unknown_element_id_keeps_label():
    c = finalize(_out(check=_chk(kind="value_contains", element_id=99, label=" 开始日期 ", expect=" 11/16 ")),
                 "zh", _els()).check
    assert c.element_id is None and c.label == "开始日期" and c.expect == "11/16"
    assert finalize(_out(check=_chk(kind="selected", element_id=12, label="x"))).check.element_id is None  # 无 elements


def test_check_valid_element_fills_label_and_box():
    c = finalize(_out(check=_chk(kind="value_contains", element_id=12)), "en", _els()).check
    assert c.element_id == 12 and c.label == "New Tab" and c.box == [40, 950, 62, 966] and c.expect is None
    c = finalize(_out(check=_chk(kind="value_contains", element_id=12, label="Tab name", box=[5, 5, 9, 9])),
                 "en", _els()).check
    assert c.label == "Tab name" and c.box == [5, 5, 9, 9]  # 模型给了就保留


def test_check_box_normalized():
    assert finalize(_out(check=_chk(kind="appears", expect="x", box=[600, 100, 500, 1200]))).check.box == \
        [500, 100, 600, 1000]
    assert finalize(_out(check=_chk(kind="appears", expect="x", box=[1, 2, 3]))).check.box is None


def test_check_requires_label_or_expect():
    for kind in ("value_contains", "appears", "disappears"):
        assert finalize(_out(check=_chk(kind=kind, label=" ", expect=""))).check is None
        assert finalize(_out(check=_chk(kind=kind, expect="Following"))).check is not None
        assert finalize(_out(check=_chk(kind=kind, label="Subject"))).check is not None
    assert finalize(_out(check=_chk(kind="selected", expect="Remember me"))).check is None
    assert finalize(_out(check=_chk(kind="selected", label="Remember me"))).check.label == "Remember me"
    assert finalize(_out(check=_chk(kind="selected", element_id=13)), "en", _els()).check is None  # 无名控件补不出 label


def test_check_dropped_when_not_next():
    c = _chk(kind="appears", expect="Following")
    assert finalize(_out(status="done", box=None, check=c)).check is None
    assert finalize(_out(box=None, check=c)).check is None  # next 没框 → unsure


def test_next_step_keeps_check_element_on_own_box(monkeypatch):
    monkeypatch.setattr(steps.gemini, "generate_json", lambda *a, **k: _out(
        box=[300, 300, 320, 320], check=_chk(kind="value_contains", element_id=12, expect="x")))  # 框不在任何控件上
    out = steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, elements=ELEMENTS))
    assert out.element_id is None and out.check.element_id == 12 and out.check.label == "New Tab"


def test_mock_returns_check(client, monkeypatch):
    out = mock_next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, elements=ELEMENTS))
    assert out.check.kind == "value_contains" and out.check.element_id == 12 and out.check.label == "New Tab"
    assert out.check.expect is None
    monkeypatch.setattr(steps.gemini, "MOCK", True)
    j = client.post("/api/next-step", json={"app": "a", "goal": "g", "screenshot": TINY_PNG_B64,
                                            "elements": ELEMENTS}).json()
    assert j["check"] == {"kind": "value_contains", "element_id": 12, "label": "New Tab", "expect": None,
                          "box": [40, 950, 62, 966]}
    j = client.post("/api/next-step", json={"app": "a", "goal": "g", "screenshot": TINY_PNG_B64}).json()
    assert "check" in j and j["check"] is None


def test_prompt_mentions_check():
    p = steps.build_prompt(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64))
    assert "check" in p and "value_contains" in p and "disappears" in p and "selected" in p
    assert "{" not in p
    p = steps.build_prompt(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, elements=ELEMENTS))
    assert "check 的指标控件如果在列表里" in p


# ---- 编号写成字符串（整数 element_id 经常停不下来：1583095034…）----
def test_gemini_schema_has_no_ids():
    # 编号字段最容易停不下来（整数 1583095034…、字符串 "49Node49…"）：给 Gemini 的格式里干脆没有编号
    assert "element_id" not in steps.NextStepRaw.model_fields
    assert "element_id" not in steps.CheckRaw.model_fields
    assert [f for f in NextStepOut.model_fields if f != "element_id"] == list(steps.NextStepRaw.model_fields)


def test_match_element_by_exact_name_then_nearest_duplicate():
    els = [steps.Element(id=1, role="AXButton", name="Save", box=[100, 100, 120, 150]),
           steps.Element(id=2, role="AXButton", name="Save", box=[800, 800, 820, 850]),
           steps.Element(id=3, role="AXButton", name="New Tab", box=[40, 950, 62, 966])]
    assert steps.match_element(els, " new tab ", None).id == 3  # 不分大小写、去空白
    assert steps.match_element(els, "Save", [795, 790, 825, 860]).id == 2  # 同名：取离模型的框最近的
    assert steps.match_element(els, "Save", [95, 95, 125, 155]).id == 1


def test_match_element_by_overlap_when_name_differs():
    els = [steps.Element(id=13, role="AXTextField", name=None, box=[120, 300, 140, 700])]
    assert steps.match_element(els, "Search box", [118, 305, 142, 690]).id == 13  # 名字对不上，但框几乎重合
    assert steps.match_element(els, "Search box", [500, 500, 520, 520]) is None  # 不重合：用模型自己的框
    assert steps.match_element([], "x", [1, 2, 3, 4]) is None


def test_from_raw_fills_ids_from_matching():
    els = [steps.Element(id=12, role="AXButton", name="New Tab", box=[40, 950, 62, 966]),
           steps.Element(id=30, role="AXStaticText", name="New Message", box=[200, 600, 220, 800])]
    raw = steps.NextStepRaw(status="next", previous_ok=None, instruction="Click New Tab", target_label="New Tab",
                            box=[41, 949, 63, 967], expect=None,
                            check=steps.CheckRaw(kind="appears", label="New Message"))
    out = steps.from_raw(raw, els)
    assert type(out) is NextStepOut and out.element_id == 12 and out.check.element_id == 30
    raw.status = "done"
    assert steps.from_raw(raw, els).element_id is None
