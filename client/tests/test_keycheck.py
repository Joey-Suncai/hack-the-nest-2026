"""关键指标检查（keycheck.py）：evaluate 每种 kind、文字 / 数字规范化、OCR 后备、controller 里怎么用。不碰真 AX。"""

import types

from PIL import Image
from PyQt6.QtCore import QCoreApplication

from showme import controller as ctl
from showme import keycheck as kc
from showme import reasoning
from showme.controller import Controller
from showme.keycheck import MET, NOT_MET, UNKNOWN, Snap

_app = QCoreApplication.instance() or QCoreApplication([])


def snap(values=None, names=(), complete=True, src="AX"):
    vals = {kc.norm(k): v for k, v in (values or {}).items()}
    return Snap(vals, frozenset(kc.norm(n) for n in (*names, *(values or {}))), complete, src)


def chk(kind, label=None, expect=None, box=None):
    return {"kind": kind, "element_id": None, "label": label, "expect": expect, "box": box}


# ---------------- 规范化 ----------------
def test_digit_and_whitespace_normalization():
    assert kc.contains("2026/11/16", "11/16")
    assert kc.contains("2026 / 11 / 16", "11/16")
    assert kc.contains("2026-11-16", "11/16")
    assert kc.contains("Sunday, November 16, 2026", "Nov 16")
    assert kc.contains("2026/11/06", "11/6")
    assert kc.contains("２０２６/１１/１６", "11/16")  # 全角数字
    assert kc.contains("  Team   Sync ", "team sync")
    assert not kc.contains("2026/11/15", "11/16")
    assert not kc.contains("2026/11/16", "")
    assert not kc.contains("Dec 16", "Nov 16")


# ---------------- evaluate ----------------
def test_value_contains():
    c = chk("value_contains", "开始日期", "11/16")
    base = snap({"开始日期": ("2026/11/15", False)})
    assert kc.evaluate(c, base, snap({"开始日期": ("2026/11/16", False)})) == MET
    assert kc.evaluate(c, base, base) == NOT_MET
    assert kc.evaluate(c, base, snap({"结束日期": ("2026/11/16", False)})) == UNKNOWN  # 控件找不到
    assert kc.evaluate(c, base, None) == UNKNOWN
    assert kc.evaluate(chk("value_contains", "开始", "11/16"), base, snap({"开始日期": ("11/16", False)})) == MET


def test_value_contains_without_expect_means_changed_and_non_empty():
    c = chk("value_contains", "Subject")
    base = snap({"Subject": ("", False)})
    assert kc.evaluate(c, base, snap({"Subject": ("Hello", False)})) == MET
    assert kc.evaluate(c, base, base) == NOT_MET
    assert kc.evaluate(c, snap({"Subject": ("Hi", False)}), snap({"Subject": ("", False)})) == NOT_MET  # 清空不算
    assert kc.evaluate(c, None, snap({"Subject": ("Hello", False)})) == UNKNOWN  # 没基准：没法比


def test_appears():
    c = chk("appears", expect="新建事件")
    assert kc.evaluate(c, snap(), snap(names=["新建事件 - 日历"])) == MET
    assert kc.evaluate(c, snap(), snap(names=["日历"])) == NOT_MET
    assert kc.evaluate(chk("appears"), snap(), snap(names=["x"])) == UNKNOWN  # 没说找什么
    assert kc.evaluate(chk("appears", label="Share"), None, snap(names=["Share Screen"])) == MET


def test_disappears():
    c = chk("disappears", expect="新建事件")
    base = snap(names=["新建事件"])
    assert kc.evaluate(c, base, snap(names=["日历"])) == MET
    assert kc.evaluate(c, base, base) == NOT_MET
    assert kc.evaluate(c, snap(names=["日历"]), snap(names=["日历"])) == UNKNOWN  # 基准里就没有
    assert kc.evaluate(c, base, snap(names=["日历"], complete=False)) == UNKNOWN  # 读到一半超时


def test_selected():
    c = chk("selected", "Mute")
    assert kc.evaluate(c, snap({"Mute": ("0", False)}), snap({"Mute": ("1", True)})) == MET
    assert kc.evaluate(c, snap({"Mute": ("0", False)}), snap({"Mute": ("0", False)})) == NOT_MET
    assert kc.evaluate(c, snap({"Mute": ("1", True)}), snap({"Mute": ("1", True)})) == NOT_MET  # 本来就选中
    assert kc.evaluate(c, None, snap({"Other": ("1", True)})) == UNKNOWN
    assert kc.evaluate(c, None, snap({"Mute": ("x", False)}, src="OCR")) == UNKNOWN  # OCR 看不出选中


def test_bad_checks_are_unknown():
    for bad in (None, {}, {"kind": "nope"}, "x", 3):
        assert kc.parse(bad) is None
        assert kc.evaluate(bad, snap(), snap()) == UNKNOWN
    assert kc.parse({"kind": "appears", "box": [5, 5, 1, 1]})["box"] is None


def test_node_entry_value_and_selected():
    d = {"AXDescription": "开始日期", "AXValue": "2026/11/15"}
    assert kc.node_entry("AXTextField", d.get) == (["开始日期"], "2026/11/15", False)
    d = {"AXTitle": "Mute", "AXValue": 1}
    assert kc.node_entry("AXCheckBox", d.get) == (["Mute"], "1", True)
    d = {"AXTitle": "Save"}  # 没有值：用名字
    assert kc.node_entry("AXButton", d.get) == (["Save"], "Save", False)


# ---------------- OCR 后备 ----------------
def test_ocr_fallback_with_fake_ocr():
    img = Image.new("RGB", (1000, 500), "white")
    seen = []

    def fake_find(part, label, fast=True):
        seen.append(part.size)
        return [("Start", [0, 0, 1, 1]), ("2026/11/16", [0, 0, 1, 1])]

    c = chk("value_contains", "开始日期", "11/16", box=[100, 200, 200, 400])
    s = kc.read_ocr(img, c, fake_find)
    assert seen == [(200, 50)] and s.src == "OCR"  # 只读框里那块
    assert kc.evaluate(c, None, s) == MET
    assert kc.evaluate(chk("value_contains", "开始日期", "11/15", box=c["box"]), None, s) == NOT_MET
    assert kc.evaluate(chk("appears", expect="Start", box=c["box"]), None, s) == MET
    gone = kc.read_ocr(img, c, lambda *a, **k: [])
    assert kc.evaluate(chk("disappears", expect="Start", box=c["box"]), s, gone) == MET

    # AX 找不到这个控件、有框 → baseline 选 OCR；没框 → 都不行
    monkey_ax = snap({"别的": ("x", False)})
    b, src = kc.baseline(c, "Zoom", (1000, 500), monkey_ax, grab=lambda: img, find=fake_find)
    assert src == "OCR" and kc.lookup(b, "开始日期")[0] == "Start 2026/11/16"
    import showme.keycheck as mod
    orig = mod.read_state
    mod.read_state = lambda *a, **k: monkey_ax
    try:
        assert kc.baseline({**c, "box": None}, "Zoom", (1000, 500), monkey_ax) == (None, None)
    finally:
        mod.read_state = orig
    assert kc.baseline(c, "Zoom", (1000, 500), snap({"开始日期": ("2026/11/15", False)}))[1] == "AX"


def test_confirmed_check_maps_to_click():
    conf = reasoning.confirmed("check", "Nov 16", "check met: 开始日期 = 2026/11/16")
    assert conf == {"kind": "click", "target": "Nov 16", "detail": "check met: 开始日期 = 2026/11/16"}


# ---------------- controller ----------------
def _ctl(monkeypatch, state=ctl.WAITING, acted_req=False):
    """controller 的假身：_bg_free 直接同步执行，probe 返回 now[0]。"""
    done, reqs, log = [], [], []
    now = [None]
    monkeypatch.setattr(kc, "probe", lambda c, src, app, wh, *a, **k: now[0])
    c = types.SimpleNamespace(state=state, _step_done=False, _token=1, _screen_wh=(1000, 800), task={"app": "Zoom"},
                              history=[], _recheck=None, _provisional=None, expect="e", _req_expect="req e",
                              _req_confirmed={"kind": "click"}, _acted_in_req=acted_req, _req_state=None,
                              _prev_front="zoom.us", _inputs_ok=True,
                              overlay=types.SimpleNamespace(clear=lambda: log.append("clear")),
                              narrator=types.SimpleNamespace(stop=lambda: log.append("hush")))
    c._bg_free = lambda fn, ok, err: ok(fn())
    c._step_completed = lambda reason, detail=None: done.append((reason, detail))
    c._request = lambda *a, **k: reqs.append((c.expect, c._confirmed))
    for name in ("_check_begin", "_check_based", "_check_after_action", "_check_eval", "_check_done",
                 "_check_reask", "_check_recheck", "_check_probe_for_request", "_check_detail"):
        setattr(c, name, types.MethodType(getattr(Controller, name), c))
    return c, now, done, reqs, log


DATE = {"kind": "value_contains", "label": "开始日期", "expect": "11/16", "element_id": None, "box": None}
BASE = snap({"开始日期": ("2026/11/15", False)})
MET_SNAP = snap({"开始日期": ("2026/11/16", False)})


def _begin(c, monkeypatch, base=BASE):
    monkeypatch.setattr(kc, "baseline", lambda check, app, wh, req=None: (base, "AX"))
    c._check_begin({"check": DATE})


def test_tick_every_1_5s_and_restarts_after_action(monkeypatch):
    c, now, done, reqs, _ = _ctl(monkeypatch)
    now[0] = BASE
    _begin(c, monkeypatch)
    ctx = c._check
    assert abs(ctx["next_t"] - ctx["t_shown"] - ctl.KEYCHECK_INTERVAL_S) < 0.01
    c._check_after_action()  # mouse up / 按键：从这次操作重新计时
    assert abs(ctx["next_t"] - ctx["acted_t"] - ctl.KEYCHECK_INTERVAL_S) < 0.01 and ctx["acts"] == 1
    evals = []
    c._check_eval = lambda cx, why, rc=None: evals.append(why)
    monkeypatch.setattr(ctl.capture, "grab", lambda: (_ for _ in ()).throw(RuntimeError("stop here")))
    c._on_error = lambda e: None
    Controller._on_poll(types.SimpleNamespace(**{**c.__dict__, "_settle": None}))  # 还没到点
    assert evals == []
    ctx["next_t"] = 0.0
    p = types.SimpleNamespace(**{**c.__dict__, "_settle": None})
    Controller._on_poll(p)
    assert evals == ["tick"] and ctx["next_t"] > ctl.time.monotonic() + 1.0


def test_met_while_waiting_completes_with_check(monkeypatch, capsys):
    c, now, done, reqs, _ = _ctl(monkeypatch)
    now[0] = BASE
    _begin(c, monkeypatch)
    out = capsys.readouterr().out
    assert "value_contains 开始日期 ⊇ '11/16'" in out and "2026/11/15" in out and done == []
    c._check_after_action()
    c._check_eval(c._check, "tick")  # 1.5 s 后：还没生效 → 框留着
    assert done == [] and "[check] not met" in capsys.readouterr().out
    now[0] = MET_SNAP
    c._check_eval(c._check, "tick")
    assert done == [("check", "check met: 开始日期 = 2026/11/16")] and reqs == []
    assert "[check] met after" in capsys.readouterr().out


def test_met_while_requesting_reasks(monkeypatch, capsys):
    # 用户在等 Gemini 时就做完了：结果回来时 check 已经成立 → 撤掉、用最新画面重问
    c, now, done, reqs, log = _ctl(monkeypatch, acted_req=True)
    now[0] = MET_SNAP
    _begin(c, monkeypatch)
    assert done == [] and log == ["clear", "hush"] and c._token == 2 and c._check is None
    assert reqs == [("req e", {"kind": "click"})]
    assert "met while Gemini was thinking" in capsys.readouterr().out


def test_recheck_in_requesting_with_check_met_reasks(monkeypatch):
    c, now, done, reqs, _ = _ctl(monkeypatch)
    now[0] = BASE
    _begin(c, monkeypatch)
    c._check["at_req"] = NOT_MET  # 发请求时还没成立
    c.state = ctl.REQUESTING
    fired = []
    rc = {"token": 1, "img": "req", "hist": 0, "confirmed": None, "expect": None}
    c._recheck_tick = lambda r: fired.append(dict(r))
    now[0] = MET_SNAP
    assert c._check_recheck(rc)  # 复查画面看不出变化 → 再读关键指标
    assert fired and fired[0]["check"] == "开始日期 = 2026/11/16" and c._recheck is rc
    c._check["at_req"] = MET  # 发请求时就已经成立：截图不早，不重问
    assert not c._check_recheck(rc)


def test_already_met_at_baseline_is_ignored(monkeypatch):
    c, now, done, reqs, _ = _ctl(monkeypatch)
    now[0] = MET_SNAP
    _begin(c, monkeypatch, base=MET_SNAP)
    c._check_after_action()
    c._check_eval(c._check, "tick")
    assert done == [] and reqs == [] and c._check["dead"]


def test_unknown_changes_nothing(monkeypatch, capsys):
    c, now, done, reqs, log = _ctl(monkeypatch)
    now[0] = snap({"别的": ("x", False)})
    _begin(c, monkeypatch)
    c._check_after_action()
    c._check_eval(c._check, "tick")
    assert done == [] and reqs == [] and log == [] and c._check["last"] is None
    monkeypatch.setattr(kc, "baseline", lambda *a, **k: (None, None))  # AX 找不到、没框
    c._check_begin({"check": DATE})
    assert "unknown (control not found, no box)" in capsys.readouterr().out
    c._check_eval(c._check, "tick")
    assert done == []


def test_click_in_box_not_met_still_completes_with_detail(monkeypatch):
    c, now, done, reqs, _ = _ctl(monkeypatch)
    now[0] = BASE
    _begin(c, monkeypatch)
    done.append(("click", None))  # _on_mouse_up 点在框里：照旧马上完成（不看 check）
    c._step_done = True
    for snap_now, want in ((BASE, "check not met: 开始日期 = 2026/11/15"), (MET_SNAP, "check met: 开始日期 = 2026/11/16")):
        now[0] = snap_now
        conf = {"kind": "click", "target": "Nov 16", "detail": None}
        c._conf_check = c._check_probe_for_request(c._check, conf)
        c._check_detail(conf)
        assert conf["detail"] == want
    assert done == [("click", None)]
    assert c._check["at_req"] == MET


def test_controller_on_mouse_up_restarts_check_timer(monkeypatch):
    from tests.test_settle_filler import _mouse

    c, done = _mouse(monkeypatch)
    c._check = {"ready": True, "dead": False, "busy": False, "acts": 0, "acted_t": None, "next_t": 0.0}
    c._check_after_action = types.MethodType(Controller._check_after_action, c)
    c._on_mouse_down(120, 210)
    c._on_mouse_up(121, 211)
    assert done == ["click"] and c._check["acts"] == 1 and c._check["next_t"] > ctl.time.monotonic() + 1.0
