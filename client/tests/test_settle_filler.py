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
    c = _settler(monkeypatch, [0.2, 0.5, 0.5, 0.5])  # 还在动 → 动 → 不动了
    c._last_raw = np.full((64, 64), 0.0, np.float32)
    c._step_completed("screen_change")
    assert c.fired == [] and c.poll.interval == 100  # 没马上截图，轮询改成 0.1 s
    _run(c)
    assert len(c.fired) == 1 and c.poll.interval == 150
    assert "稳定了" in capsys.readouterr().out


def test_click_waits_at_least_min_even_if_stable(monkeypatch):
    c = _settler(monkeypatch, [0.3] * 100)
    t0 = time.monotonic()
    c._step_completed("click")
    _run(c)
    assert c.fired and c.fired[0] - t0 >= ctl.SETTLE_MIN_CLICK_S


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
