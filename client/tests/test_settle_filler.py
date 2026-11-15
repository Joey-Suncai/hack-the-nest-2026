"""截图前等画面稳定、调用 tool 时先念一句（缓存）、OCR 框被 Gemini 否掉时马上收回。不出声、不截真屏。"""

import threading
import time
import types

import numpy as np
from PyQt6.QtCore import QCoreApplication

from showme import capture
from showme import controller as ctl
from showme import narrator as narrator_mod
from showme import speech
from showme.controller import FILLERS, Controller

_app = QCoreApplication.instance() or QCoreApplication([])


# ---------------- 等画面稳定 ----------------
class FakePoll:
    def __init__(self): self.interval = None
    def setInterval(self, ms): self.interval = ms
    def isActive(self): return True
    def start(self): pass


def _settler(monkeypatch, frames):
    """frames：每次截缩略图依次返回的帧（数字 = 灰度值）。"""
    it = iter(frames)
    monkeypatch.setattr(capture, "grab", lambda: None)
    monkeypatch.setattr(capture, "to_small_gray", lambda img: np.full((64, 64), next(it), np.float32))
    c = types.SimpleNamespace(poll=FakePoll(), fired=[], _grow_masks=lambda: False, _masked=lambda raw: raw,
                              _last_raw=None, _settle=None, state=ctl.WAITING, _step_done=False,
                              last_instruction="Click File", _t_change=None, _step_reason=None)
    c._on_screen_changed = lambda: c.fired.append(time.monotonic())
    for name in ("_step_completed", "_settle_tick"):
        setattr(c, name, types.MethodType(getattr(Controller, name), c))
    return c


def _run(c, max_ticks=40):
    n = 0
    while c._settle is not None and n < max_ticks:
        c._settle_tick()
        n += 1
        if c._settle is not None:
            time.sleep(0.02)
    return n


def test_screen_change_waits_until_two_frames_match(monkeypatch, capsys):
    c = _settler(monkeypatch, [0.2, 0.5] + [0.5] * 60)  # 还在动 → 不动了
    c._step_completed("screen_change")
    assert c.fired == [] and c.poll.interval == 100  # 没马上截图，轮询改成 0.1 s
    _run(c)
    assert len(c.fired) == 1 and c.poll.interval == 150
    out = capsys.readouterr().out
    assert "稳定了" in out and "1 帧" not in out  # 不拿触发那一帧当起点：至少比过两张新截的


def test_click_waits_at_least_min_even_if_stable(monkeypatch):
    c = _settler(monkeypatch, [0.3] * 100)
    t0 = time.monotonic()
    c._step_completed("click")
    _run(c)
    assert c.fired and c.fired[0] - t0 >= ctl.SETTLE_MIN_S


def test_never_stable_gives_up_at_max(monkeypatch, capsys):
    rng = np.random.default_rng(1)
    c = _settler(monkeypatch, list(rng.random(400)))  # 视频：每帧都不一样
    c._step_completed("screen_change")
    c._settle["t0"] -= ctl.SETTLE_MAX_S  # 假装已经等满 1.5 秒
    c._settle_tick()
    assert c.fired and c._settle is None
    assert "到时间了" in capsys.readouterr().out


def test_only_once_per_step(monkeypatch):
    c = _settler(monkeypatch, [0.1] * 50)
    c._step_completed("click")
    c._step_completed("screen_change")  # 等稳定时又触发：忽略
    _run(c)
    assert len(c.fired) == 1


# ---------------- 缓存的 "Let me look." ----------------
class FakePlayer:
    def __init__(self): self.pushed = []
    def warmup(self, sr=44100): pass
    def push(self, x, sr, on_start=None): self.pushed.append(float(x[0]))
    def clear(self): pass
    def playing(self): return False


class FakeOverlay:
    def show_caption(self, t): pass
    def clear_caption(self): pass


def test_prefetched_phrase_plays_without_network(monkeypatch):
    monkeypatch.setattr(speech, "decode_mp3", lambda b: (np.full(800, 7.0, np.float32), 44100.0))
    calls = []

    def stream(text, lang):
        calls.append(text)
        yield b"mp3" * 100

    p = FakePlayer()
    n = narrator_mod.Narrator(FakeOverlay(), None, lambda: True, lambda: "en", player=p, stream=stream)
    n.prefetch(["Let me look."])
    t = time.time() + 3
    while not n.cached("Let me look.") and time.time() < t:
        time.sleep(0.01)
    assert n.cached("Let me look.") and calls == ["Let me look."]
    n.say("Let me look.", interrupt=True)
    assert p.pushed == [7.0]  # 当场就交给播放器了
    assert calls == ["Let me look."]  # 没有再请求 /api/speak
    assert n.current.done


def test_filler_alternates_and_keeps_agent_audio(monkeypatch):
    said = []
    audio = types.SimpleNamespace(drop_stale=False, drop_for_client=lambda: said.append("DROP"))
    c = types.SimpleNamespace(_talking=False, _n_filler=0, panel=types.SimpleNamespace(voice_enabled=lambda: True),
                              narrator=types.SimpleNamespace(cached=lambda t: True,
                                                             say=lambda t, interrupt=False, tag=None: said.append((t, interrupt))),
                              agent=types.SimpleNamespace(audio=audio))
    f = types.MethodType(Controller._filler, c)
    f(); f(); f()
    assert said == [(FILLERS[0], True), (FILLERS[1], True), (FILLERS[0], True)]  # 不调 drop_for_client：Agent 的回答还要放
    c._talking = True
    f()
    assert len(said) == 3  # 用户正在说话：不念


# ---------------- OCR 框被否掉 ----------------
def test_ocr_box_then_gemini_done_stops_and_clears(monkeypatch):
    log = []
    panel = types.SimpleNamespace(set_status=lambda *a: None, add_message=lambda *a: log.append(("msg",) + a),
                                  set_steps=lambda *a: None, allow_retry=lambda *a: None)
    c = types.SimpleNamespace(
        history=[{"instruction": "Click OK", "ok": True}], panel=panel, task={"app": "RStudio", "goal": "make a file"},
        lang="en", _provisional={"box": [1, 2, 3, 4], "instruction": "Click Knit"}, _speak_after_reply=True,
        last_instruction="Click Knit", agent=None, state=ctl.REQUESTING,
        narrator=types.SimpleNamespace(stop=lambda: log.append("stop")),
        overlay=types.SimpleNamespace(clear=lambda: log.append("clear")),
        _remember=lambda *a: None, _end_task=lambda *a: log.append("end"),
        _speak_step=lambda text, tag=None: log.append(("speak", text)))
    c._on_result = types.MethodType(Controller._on_result, c)
    c._on_result({"status": "done", "instruction": "You created the file!"})
    assert log.index("stop") < log.index("clear") < next(i for i, x in enumerate(log) if isinstance(x, tuple) and x[0] == "speak")
    assert log[-1] == "end" and c.last_instruction is None and c._speak_after_reply is False


# ---------------- 每一步只认一个信号 ----------------
class _Rect:
    def x(self): return 100
    def y(self): return 200
    def width(self): return 50
    def height(self): return 30


def _signals(monkeypatch, kind, inputs_ok=True, fired=True):
    monkeypatch.setattr(capture, "grab", lambda: None)
    monkeypatch.setattr(capture, "to_small_gray", lambda img: np.zeros((64, 64), np.float32))
    done = []
    c = types.SimpleNamespace(state=ctl.WAITING, _step_done=False, _settle=None, _kind=kind, _inputs_ok=inputs_ok,
                              _rebase=False, _provisional=None, _base_raw=np.zeros((64, 64), np.float32),
                              overlay=types.SimpleNamespace(_rect=_Rect()), _grow_masks=lambda: False,
                              _masked=lambda raw: raw, _last_raw=None,
                              detector=types.SimpleNamespace(feed=lambda f: fired, reset=lambda f: None))
    c._step_completed = lambda reason, detail=None: done.append(reason)
    c._down = None
    c.task, c._token, c.history = {"goal": "x"}, 0, []
    for name in ("_on_click", "_on_mouse_down", "_on_mouse_up", "_click_completes", "_on_key", "_on_poll",
                 "_screen_only_backup", "_cancel_recheck", "_schedule_miss_recheck", "_miss_recheck_tick"):
        setattr(c, name, types.MethodType(getattr(Controller, name), c))
    return c, done


def test_typing_step_only_enter(monkeypatch):
    c, done = _signals(monkeypatch, "type")
    c._on_click(120, 210)  # 点输入框：打字的一部分
    c._on_key(False)  # 打字
    c._on_poll()  # 画面在变（下拉框、打的字）
    assert done == []
    c._on_key(True)
    assert done == ["enter"]


def test_key_step_any_key(monkeypatch):
    c, done = _signals(monkeypatch, "key", fired=False)
    c._on_key(False)
    assert done == ["key"]


def test_click_step_ignores_screen_change_and_clicks_outside_box(monkeypatch):
    c, done = _signals(monkeypatch, "click")
    c._on_poll()  # 加载 / 悬停：画面变了但没点
    assert done == [] and c._rebase
    c._on_click(400, 600)  # 点得很远
    assert done == []
    c._on_click(75, 205)  # 框外 25 点（放宽 8 点之外）：不算（框来自真实控件，已经准了）
    assert done == []


class _FakeOverlay:
    """记录 suspend / resume；suspend 后 _rect 变 None（和真的一样），resume 画回来。"""
    def __init__(self):
        self._rect, self.calls = _Rect(), []

    def suspend(self):
        self.calls.append("suspend")
        self._saved, self._rect = self._rect, None
        return True

    def resume(self):
        self.calls.append("resume")
        self._rect = self._saved


def _mouse(monkeypatch, kind="click"):
    c, done = _signals(monkeypatch, kind, fired=False)
    c.overlay = _FakeOverlay()
    c._down = None
    for name in ("_on_mouse_down", "_on_mouse_up", "_click_completes"):
        setattr(c, name, types.MethodType(getattr(Controller, name), c))
    return c, done


def test_mouse_down_hides_box_and_up_inside_completes(monkeypatch):
    c, done = _mouse(monkeypatch)
    c._on_mouse_down(120, 210)
    assert c.overlay.calls == ["suspend"] and done == []  # 按下：框马上藏起来，还不算完成
    c._on_mouse_up(121, 211)
    assert done == ["click"] and c.overlay.calls == ["suspend"]  # 松开在框里：完成，框不再画回来


def test_drag_out_of_box_does_not_complete_and_box_comes_back(monkeypatch):
    c, done = _mouse(monkeypatch)
    c._on_mouse_down(120, 210)  # 框里按下
    c._on_mouse_up(400, 600)  # 拖到框外松开（取消 / 拖拽）
    assert done == [] and c.overlay.calls == ["suspend", "resume"] and c.overlay._rect is not None


def test_press_outside_then_release_inside_does_not_complete(monkeypatch):
    c, done = _mouse(monkeypatch)
    c._on_mouse_down(400, 600)
    c._on_mouse_up(120, 210)
    assert done == [] and c.overlay.calls == ["suspend", "resume"]


def test_mouse_up_without_down_is_ignored(monkeypatch):
    c, done = _mouse(monkeypatch)
    c._on_mouse_up(120, 210)  # 按下时还没有框（比如在等 Gemini）
    assert done == [] and c.overlay.calls == []


def test_state_change_between_down_and_up_does_not_complete(monkeypatch):
    c, done = _mouse(monkeypatch)
    c._on_mouse_down(120, 210)
    c.state = ctl.PAUSED  # 按住的时候用户按了停止 / 开始说话
    c._on_mouse_up(120, 210)
    assert done == [] and c.overlay.calls == ["suspend", "resume"]


def test_click_in_box_completes(monkeypatch):
    c, done = _signals(monkeypatch, "click", fired=False)
    c._on_click(120, 210)
    assert done == ["click"]


def test_screen_change_counts_without_accessibility_or_without_box(monkeypatch):
    c, done = _signals(monkeypatch, "click", inputs_ok=False)
    c._on_poll()
    assert done == ["screen_change"]
    c, done = _signals(monkeypatch, "none")  # unsure 之后没有框：画面变了就再看一次
    c._on_poll()
    assert done == ["screen_change"]


def test_agent_cannot_replace_shown_step_by_itself():
    calls = []
    c = types.SimpleNamespace(_stop_epoch=0, task={"goal": "x"}, state=ctl.WAITING, last_instruction="Click Clone",
                              overlay=types.SimpleNamespace(_rect=_Rect()), _user_turn_since_step=False, _sync_step=2,
                              main=types.SimpleNamespace(call=lambda f: f()), _filler=lambda: calls.append("filler"))
    c._tool_prepare = lambda goal, note: calls.append("screenshot")
    out = Controller.tool_show_next_step(c, {"goal": "clone"})
    assert out == "SHOWN: Click Clone" and calls == []  # 不念 filler、不截图


def test_settle_waits_for_change_before_stable(monkeypatch, capsys):
    c = _settler(monkeypatch, [0.0] * 4 + [0.5] * 60)  # 点完先没反应（网页还在请求），之后才变
    c._last_raw = np.zeros((64, 64), np.float32)  # 点之前的画面
    c._step_completed("click")
    _run(c, max_ticks=80)
    out = capsys.readouterr().out
    assert c.fired and "开始变，之后稳定了" in out


def test_settle_no_change_gives_up_at_max(monkeypatch, capsys):
    c = _settler(monkeypatch, [0.0] * 400)
    c._last_raw = np.zeros((64, 64), np.float32)
    c._step_completed("click_near")  # 点在框里的（click）不等慢窗口：见 test_confirmed_click_waits_only_short
    c._settle_tick()
    assert c.fired == []  # 一直没变：不会 0.3 s 就截
    c._settle["t0"] -= ctl.SETTLE_MAX_S
    c._settle_tick()
    assert c.fired == []  # 1.5 s 了还没变：慢窗口（Zoom「安排会议」）→ 继续等
    c._settle["t0"] -= ctl.SETTLE_NOCHANGE_MAX_S - ctl.SETTLE_MAX_S
    c._settle_tick()
    assert c.fired and "画面一直没变，到时间了" in capsys.readouterr().out


class FakeClock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def _sim(monkeypatch, frame_at, reason="click_near", max_s=10.0):
    """用假时钟按 SETTLE_POLL_S 一帧帧跑：frame_at(t) = t 秒时截到的画面（灰度值）。返回截正式图的时刻（秒）。"""
    clock = FakeClock()
    monkeypatch.setattr(ctl.time, "monotonic", clock)
    t0 = clock.t
    c = _settler(monkeypatch, [])
    monkeypatch.setattr(capture, "to_small_gray", lambda img: np.full((64, 64), frame_at(clock.t - t0), np.float32))
    c._on_screen_changed = lambda: c.fired.append(clock.t - t0)
    c._last_raw = np.zeros((64, 64), np.float32)  # 点之前的画面
    c._step_completed(reason)
    while c._settle is not None and clock.t - t0 < max_s:
        clock.t += ctl.SETTLE_POLL_S
        c._settle_tick()
    return c.fired


def test_slow_window_waits_past_old_max_then_captures_after_change(monkeypatch):
    fired = _sim(monkeypatch, lambda t: 0.0 if t < 3.0 else 0.5)  # 3 秒后窗口才弹出
    assert len(fired) == 1 and 3.0 < fired[0] <= 3.0 + 0.5  # 变了之后很快截，不是 1.5 s 就截旧画面


def test_fast_change_still_captures_fast(monkeypatch):
    fired = _sim(monkeypatch, lambda t: 0.0 if t < 0.1 else 0.5)  # 普通菜单：马上变
    assert len(fired) == 1 and ctl.SETTLE_MIN_S <= fired[0] <= 0.6


def test_never_changes_gives_up_at_nochange_max(monkeypatch):
    fired = _sim(monkeypatch, lambda t: 0.0)
    assert len(fired) == 1 and abs(fired[0] - ctl.SETTLE_NOCHANGE_MAX_S) < 0.15


def test_keeps_moving_after_change_capped(monkeypatch):
    fired = _sim(monkeypatch, lambda t: 0.0 if t < 2.0 else (t * 7.3) % 1.0)  # 2 秒后开始一直动（动画 / 视频）
    assert len(fired) == 1 and abs(fired[0] - (2.0 + ctl.SETTLE_MAX_S)) < 0.25


def test_stop_during_wait_aborts(monkeypatch):
    c = _settler(monkeypatch, [0.0] * 50)
    c._last_raw = np.zeros((64, 64), np.float32)
    c._step_completed("click")
    c._settle_tick()
    c.state = ctl.PAUSED  # 用户按停止 / 开始说话
    c._on_poll = types.MethodType(Controller._on_poll, c)
    c._settle["t0"] -= ctl.SETTLE_NOCHANGE_MAX_S
    c._on_poll()
    assert c.fired == []


def test_click_on_ocr_box_before_gemini_confirms(monkeypatch):
    c, done = _signals(monkeypatch, "click", fired=False)
    c.state = ctl.REQUESTING
    c._provisional = {"box": [1, 2, 3, 4], "instruction": "Click Subject"}
    c._token, c._t_change, c._req_img = 5, 1.0, None
    c._wait = lambda: setattr(c, "state", ctl.WAITING)
    c._adopt_provisional = types.MethodType(Controller._adopt_provisional, c)
    c._on_click(120, 210)
    assert done == ["click"] and c._token == 6 and c._provisional is None  # Gemini 核对作废
    assert c.current["instruction"] == "Click Subject" and c._t_change == 1.0


def test_click_elsewhere_during_ocr_box_keeps_waiting_for_gemini(monkeypatch):
    c, done = _signals(monkeypatch, "click", fired=False)
    c.state = ctl.REQUESTING
    c._provisional = {"box": [1, 2, 3, 4], "instruction": "Click Subject"}
    c._token = 5
    c._on_click(600, 900)
    assert done == [] and c._token == 5 and c._provisional is not None


# ---------------- 复查：截早了就用最新画面重新问 ----------------
def _recheck_ctl(monkeypatch, frames):
    """frames：复查时截到的缩略图灰度值；请求那张固定是 0。"""
    shots = iter(frames)
    monkeypatch.setattr(capture, "grab", lambda: next(shots))
    monkeypatch.setattr(capture, "to_small_gray", lambda img: np.full((64, 64), 0.0 if img == "req" else img, np.float32))
    monkeypatch.setattr(ctl.apps, "frontmost_app", lambda: "Zoom")
    timers = []
    monkeypatch.setattr(ctl.QTimer, "singleShot", staticmethod(lambda ms, fn: timers.append((ms, fn))))
    requests, log = [], []
    c = types.SimpleNamespace(task={"app": "Zoom", "goal": "x"}, state=ctl.REQUESTING, _token=3, history=[1, 2],
                              _step_done=False, _provisional={"box": [1, 2, 3, 4]}, expect="new", _confirmed=None,
                              _done_mono=ctl.time.monotonic(), _grow_masks=lambda: False, _masked=lambda raw: raw,
                              overlay=types.SimpleNamespace(clear=lambda: log.append("clear")),
                              narrator=types.SimpleNamespace(stop=lambda: log.append("hush")))
    c._request = lambda img=None, front=None: requests.append((img, front, c._confirmed, c.expect))
    for name in ("_schedule_recheck", "_cancel_recheck", "_recheck_tick"):
        setattr(c, name, types.MethodType(getattr(Controller, name), c))
    return c, timers, requests, log


def test_recheck_fires_about_1_5s_after_the_action(monkeypatch):
    c, timers, requests, _ = _recheck_ctl(monkeypatch, [])
    c._schedule_recheck("req", {"kind": "click", "target": "Save"}, "old")
    (ms, _fn), = timers
    assert abs(ms - ctl.RECHECK_AFTER_S * 1000) < 100


def test_recheck_no_change_keeps_result(monkeypatch, capsys):
    c, timers, requests, _ = _recheck_ctl(monkeypatch, [0.0])
    c._schedule_recheck("req", None, "old")
    timers[0][1]()
    assert requests == [] and c._token == 3 and "画面没变" in capsys.readouterr().out


def test_recheck_change_while_requesting_asks_again_with_latest_screen(monkeypatch):
    c, timers, requests, log = _recheck_ctl(monkeypatch, [0.6])
    conf = {"kind": "click", "target": "Save"}
    c._schedule_recheck("req", conf, "old")
    timers[0][1]()
    assert c._token == 4 and c._provisional is None and log == []  # 旧结果作废；还没显示，不用撤
    assert requests == [(0.6, "Zoom", conf, "old")]  # 最新画面、照样带 confirmed、用请求时的 expect


def test_recheck_change_after_result_shown_withdraws_it(monkeypatch):
    c, timers, requests, log = _recheck_ctl(monkeypatch, [0.6])
    c._schedule_recheck("req", None, "old")
    c.state = ctl.WAITING  # 按旧画面算的下一步已经显示了
    timers[0][1]()
    assert log == ["clear", "hush"] and len(requests) == 1


def test_recheck_cancelled_by_new_user_action_or_next_step(monkeypatch):
    c, timers, requests, _ = _recheck_ctl(monkeypatch, [0.6, 0.6])
    c._schedule_recheck("req", None, "old")
    c._cancel_recheck()  # 用户又点 / 按键了
    timers[0][1]()
    c._schedule_recheck("req", None, "old")
    c.history.append(3)  # 已经进到下一步
    timers[1][1]()
    assert requests == []


def test_recheck_still_runs_while_gemini_is_thinking(monkeypatch, capsys):
    # 真实情况：1.5 s 时 Gemini 还没回来，state=REQUESTING、_step_done 仍是上一步的 True。以前这里直接跳过
    c, timers, requests, _ = _recheck_ctl(monkeypatch, [0.6])
    c._step_done = True
    c._schedule_recheck("req", None, "old")
    timers[0][1]()
    assert len(requests) == 1 and "又变了" in capsys.readouterr().out


def _miss_ctl(monkeypatch, frame):
    c, done = _mouse(monkeypatch)
    timers = []
    monkeypatch.setattr(ctl.QTimer, "singleShot", staticmethod(lambda ms, fn: timers.append((ms, fn))))
    monkeypatch.setattr(capture, "grab", lambda: "now")
    monkeypatch.setattr(capture, "to_small_gray", lambda img: np.full((64, 64), frame, np.float32))
    c.task, c._token, c.history, c._recheck = {"goal": "x"}, 1, [], None
    c._last_raw = np.zeros((64, 64), np.float32)  # 点之前的画面
    for name in ("_schedule_miss_recheck", "_miss_recheck_tick"):
        setattr(c, name, types.MethodType(getattr(Controller, name), c))
    return c, done, timers


def test_miss_keeps_box_and_rechecks_unchanged_screen(monkeypatch, capsys):
    c, done, timers = _miss_ctl(monkeypatch, 0.0)
    c._on_mouse_down(400, 600)
    c._on_mouse_up(400, 600)  # 点在框外
    assert c.overlay._rect is not None and c.overlay.calls == ["suspend", "resume"]  # 框画回来、保持
    (ms, fn), = timers
    assert abs(ms - ctl.RECHECK_AFTER_S * 1000) < 1
    fn()
    assert done == [] and c.overlay._rect is not None and "画面没变" in capsys.readouterr().out


def test_miss_with_screen_change_asks_again(monkeypatch, capsys):
    c, done, timers = _miss_ctl(monkeypatch, 0.6)
    c._on_mouse_down(400, 600)
    c._on_mouse_up(400, 600)
    timers[0][1]()
    assert done == ["click_changed"] and c.overlay._rect is not None  # 重新看；框留着，直到新的一步出来
    assert "画面变了" in capsys.readouterr().out


def test_miss_recheck_cancelled_by_next_click(monkeypatch):
    c, done, timers = _miss_ctl(monkeypatch, 0.6)
    c._on_mouse_down(400, 600)
    c._on_mouse_up(400, 600)
    c._on_mouse_down(120, 210)  # 1.5 s 内又点了一下（点中了框）
    c._on_mouse_up(120, 210)
    timers[0][1]()
    assert done == ["click"]  # 复查作废，只算这一次点中
