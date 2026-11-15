"""deep / confirmed / app_switched_from / plan / 打字完成 / 弹出窗口里的控件（不碰真的 AX、不开界面）。"""

import types

from showme import ax_tree, capture, reasoning
from showme import controller as ctl
from showme.controller import Controller
from tests.test_ax_tree import H, W, _el, _FakeAX
from tests.test_settle_filler import _sim, _signals


# ---------------- deep ----------------
def test_deep_rules():
    h = [{"instruction": "Click New", "ok": True}]
    assert reasoning.want_deep([], None, None, None, [])  # 刚开始
    assert not reasoning.want_deep(h, None, {"status": "next", "previous_ok": True}, None, [])
    assert reasoning.want_deep(h, "zoom.us", None, None, [])  # 换了软件
    assert reasoning.want_deep(h, None, {"status": "unsure"}, None, [])
    assert reasoning.want_deep(h, None, {"status": "next", "previous_ok": False}, None, [])
    assert reasoning.want_deep(h, None, None, "user says done", [])
    assert reasoning.want_deep(h, None, None, None, [("Click Start", "开始时间"), ("Click start time", "开始时间")])
    assert reasoning.want_deep(h, None, None, None, [("Click Save", "Save"), ("click save", "Save button")])
    assert not reasoning.want_deep(h, None, None, None, [("Click Save", "Save"), ("Click Done", "Done")])
    assert not reasoning.want_deep(h, None, None, None, [(None, None), (None, None)])


# ---------------- confirmed ----------------
def test_confirmed_kinds():
    assert reasoning.confirmed("click", "开始时间") == {"kind": "click", "target": "开始时间", "detail": None}
    assert reasoning.confirmed("typing", None)["kind"] == "typing"
    assert reasoning.confirmed("enter", "Subject", "focused AXTextArea")["detail"] == "focused AXTextArea"
    for r in ("screen_change", "click_near", "key", None):
        assert reasoning.confirmed(r, "x") is None


def test_focus_detail():
    assert ax_tree.focus_detail(("AXComboBox", "开始时间", "Schedule"), "Schedule") == "focused AXComboBox '开始时间'"
    assert ax_tree.focus_detail(("AXWebArea", None, "Google Calendar"), "Zoom") == \
        "focused AXWebArea; new window 'Google Calendar'"
    assert ax_tree.focus_detail((None, None, "Google Calendar"), None) is None  # 不知道之前的窗口：不说「新窗口」
    assert ax_tree.focus_detail(None, "x") is None


def test_step_completed_records_confirmed(monkeypatch):
    monkeypatch.setattr(capture, "grab", lambda: None)
    for reason, kind in (("click", "click"), ("typing", "typing"), ("enter", "enter"), ("screen_change", None)):
        c = types.SimpleNamespace(poll=types.SimpleNamespace(setInterval=lambda ms: None, isActive=lambda: True),
                                  state=ctl.WAITING, _step_done=False, _last_raw=None, last_instruction="x",
                                  current={"target_label": "开始时间"})
        Controller._step_completed(c, reason)
        assert (c._confirmed or {}).get("kind") == kind
        if kind:
            assert c._confirmed["target"] == "开始时间"


def test_confirmed_click_waits_only_short(monkeypatch):
    fired = _sim(monkeypatch, lambda t: 0.0, reason="click")  # 点了，画面一直没变（只是聚焦了输入框）
    assert len(fired) == 1 and abs(fired[0] - ctl.CLICK_SETTLE_MAX_S) < 0.15
    fired = _sim(monkeypatch, lambda t: 0.0 if t < 0.3 else 0.5, reason="click")  # 0.8 s 之内变了：等它稳定
    assert len(fired) == 1 and 0.3 < fired[0] <= 0.8
    fired = _sim(monkeypatch, lambda t: 0.0, reason="screen_change")  # 画面变化触发：照旧
    assert len(fired) == 1


# ---------------- 换软件 ----------------
def test_app_switch_ignores_skipped_apps():
    assert reasoning.switched_from("zoom.us", "Google Chrome") == "zoom.us"
    assert reasoning.switched_from("zoom.us", "zoom.us") is None
    assert reasoning.switched_from(None, "Google Chrome") is None
    for skip in ("ShowMe", "Terminal", "Claude", "Python", "python3.12", None):
        assert reasoning.switched_from("zoom.us", skip) is None
        assert reasoning.switched_from(skip, "zoom.us") is None
    assert reasoning.ax_app("Google Chrome", "Zoom") == "Google Chrome"
    assert reasoning.ax_app("Terminal", "Zoom") == "Zoom"
    assert reasoning.ax_app(None, "Zoom") == "Zoom"


def _fields_ctl(**kw):
    c = types.SimpleNamespace(_prev_front=None, _confirmed=None, _last_result=None, _recent=[],
                              history=[{"instruction": "a", "ok": True}], task={"app": "Zoom", "goal": "g"})
    c.__dict__.update(kw)
    return c


def test_next_fields_switch_confirmed_deep():
    c = _fields_ctl(_prev_front="zoom.us", _confirmed={"kind": "click", "target": "Save", "detail": None},
                    _last_result={"status": "next", "previous_ok": True})
    p = {"app": "Zoom", "frontmost_app": "Google Chrome"}
    assert Controller._next_fields(c, p, None) == "Google Chrome"  # 读 Chrome 的控件，不是 Zoom
    assert p["app_switched_from"] == "zoom.us" and p["deep"] is True and p["confirmed"]["kind"] == "click"
    assert c._confirmed is None and c._prev_front == "Google Chrome"

    p = {"app": "Zoom", "frontmost_app": "Terminal"}  # 用户在终端里：不算换软件，读任务的软件
    assert Controller._next_fields(c, p, None) == "Zoom"
    assert "app_switched_from" not in p and "deep" not in p and "confirmed" not in p
    assert c._prev_front == "Google Chrome"


# ---------------- plan / situation ----------------
def test_plan_replaces_guide_and_app_follows():
    sent, steps = [], []
    c = types.SimpleNamespace(guide=["old"], task={"app": "Zoom", "goal": "g"}, _recent=[],
                              agent=types.SimpleNamespace(active=True, context=sent.append),
                              _update_steps=lambda: steps.append(list(c.guide)))
    r = {"status": "next", "box": [1, 2, 3, 4], "instruction": "Click Save", "target_label": "Save",
         "plan": ["Open Calendar", " ", "Click Save"], "app": "Google Chrome",
         "situation": "Calendar opened in Chrome", "reasoning": "Save is the last step"}
    Controller._apply_reasoning(c, r)
    assert c.guide == ["Open Calendar", "Click Save"] and steps == [c.guide]
    assert c.task["app"] == "Google Chrome"
    assert sent == ["[step] Situation: Calendar opened in Chrome Why: Save is the last step"]
    assert c._recent == [("Click Save", "Save")] and c._last_result is r

    Controller._apply_reasoning(c, {"status": "unsure", "plan": [], "situation": None})  # 空 plan：不动
    assert c.guide == ["Open Calendar", "Click Save"] and len(sent) == 1
    assert reasoning.plan_of({"plan": "not a list"}) is None


# ---------------- 打字完成 ----------------
def test_typing_then_click_or_tab_is_typing(monkeypatch):
    c, done = _signals(monkeypatch, "type")
    c._typed_keys = 0
    c._on_click(120, 210)  # 还没打字：点输入框
    c._on_key(False, True)  # 没打字先按 Tab：不算
    assert done == []
    c._on_key(False)
    c._on_click(600, 900)  # 打完字点别处（Gmail 正文）
    assert done == ["typing"]

    c, done = _signals(monkeypatch, "type")
    c._typed_keys = 0
    c._on_key(False)
    c._on_key(False, True)  # 打完字按 Tab
    assert done == ["typing"]


def test_typing_then_return_is_enter(monkeypatch):
    c, done = _signals(monkeypatch, "type")
    c._typed_keys = 0
    c._on_key(False)
    c._on_key(True)
    assert done == ["enter"]


# ---------------- 弹出的小窗口（Zoom 时间下拉列表）----------------
def test_popup_window_items_collected_small_first():
    row = _el("AXRow", 300, 400, 80, 20, kids=[_el("AXCell", 300, 400, 80, 20, "06:30")])
    item = _el("AXMenuItem", 300, 430, 80, 20, "07:00")
    txt = _el("AXStaticText", 300, 460, 80, 20, "07:30")
    popup = _el("AXWindow", 290, 390, 120, 200, kids=[row, item, txt], AXSubrole="AXUnknown")
    label = _el("AXStaticText", 50, 300, 80, 20, "Topic")  # 主窗口里的普通标签：不收
    listed = _el("AXList", 50, 500, 200, 100, kids=[_el("AXStaticText", 60, 510, 80, 20, "Daily")])
    main = _el("AXWindow", 0, 30, 800, 700, kids=[label, listed, _el("AXButton", 10, 50, 40, 20, "Save")])
    other_doc = _el("AXWindow", 0, 0, 1000, 900, title="other doc")

    wk = ax_tree._Walker(_FakeAX(), (W, H), deadline=float("inf"))
    small, big = wk.other_windows([other_doc, popup, main], main)
    assert small == [popup] and big == [other_doc]
    for w in small:
        wk.window(w, main=False)
    wk.window(main)
    got = [(r, n) for r, n, _ in wk.out]
    assert got[:4] == [("AXRow", None), ("AXCell", "06:30"), ("AXMenuItem", "07:00"), ("AXStaticText", "07:30")]
    assert ("AXStaticText", "Daily") in got and ("AXButton", "Save") in got
    assert ("AXStaticText", "Topic") not in got


def test_other_windows_skips_minimized_and_offscreen():
    mini = _el("AXWindow", 10, 10, 100, 100, AXMinimized=True)
    off = _el("AXWindow", 3000, 10, 100, 100)
    wk = ax_tree._Walker(_FakeAX(), (W, H), deadline=float("inf"))
    assert wk.other_windows([mini, off, None], None) == ([], [])


def test_new_request_fields_never_crash_on_bad_response():
    c = types.SimpleNamespace(guide=None, task=None, agent=None)
    Controller._apply_reasoning(c, {"status": "next", "box": [1, 2, 3, 4], "plan": [1, None], "app": 5,
                                    "situation": 3, "reasoning": None})
    assert c.guide is None


# ---------------- 换目标 = 新任务（不沿用旧任务的历史 / 计划）----------------
def test_goal_change_starts_new_task_even_with_correction_note():
    from showme.controller import _starts_new_task
    old = "add a friend in Spotify"
    # 日志里的真实调用：Agent 纠正目标时带 note，以前带 note 一律不算新任务
    assert _starts_new_task(old, "use the Canvas app for classes", "user is correcting the goal")
    assert _starts_new_task(old, "use a canvas", None)
    assert _starts_new_task(old, "log in to the Canvas website", "user wants to use Canvas")


def test_progress_notes_keep_the_same_task():
    from showme.controller import _starts_new_task
    old = "schedule a meeting for tomorrow afternoon"
    assert not _starts_new_task(old, "schedule a meeting for tomorrow afternoon", "user says done")
    assert not _starts_new_task(old, "schedule the zoom meeting", "user clicked the wrong button")  # 措辞变了，但是进度
    assert not _starts_new_task(old, "finish scheduling", "user says they have opened the schedule")
    assert not _starts_new_task(old, None, "user says done")
