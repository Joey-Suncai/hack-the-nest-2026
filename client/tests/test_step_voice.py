"""框和声音同步：客户端念步骤（只念当前这一步、晚到的语音丢掉）、Agent 声音不重叠、tool 返回值、[step] 背景消息。
全部用假的播放器 / 假的语音流，不会真的出声。"""

import os
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QCoreApplication

from showme import narrator as narrator_mod
from showme import speech
from showme.audio_engine import FilteredAgentAudio
from showme.controller import Controller, tool_reply
from showme.voice_filter import Gate

_app = QCoreApplication.instance() or QCoreApplication([])
SAMPLE_MP3 = Path("/System/Library/PrivateFrameworks/PersonalAudio.framework/Versions/A/Resources/Enrollment_1.mp3")


# ---------------- 边下边解码 ----------------
@pytest.mark.skipif(not SAMPLE_MP3.exists(), reason="没有系统自带的 mp3 样本")
def test_stream_decoder_matches_full_decode():
    data = SAMPLE_MP3.read_bytes()[:60000]
    full, sr = speech.decode_mp3(data)
    dec, parts = speech.StreamDecoder(), []
    rng = np.random.default_rng(0)
    i = 0
    while i < len(data):
        n = int(rng.integers(200, 3000))
        parts += dec.feed(data[i:i + n])
        i += n
    first_at = len(parts)
    parts += dec.finish()
    assert first_at > 3  # 下载过程中就已经交出了好几段（不是等到最后）
    out = np.concatenate([x for x, _ in parts])
    assert {s for _, s in parts} == {sr}
    assert len(out) == len(full) and np.allclose(out, full, atol=1e-5)


def test_stream_decoder_holds_back_margin_until_finish():
    dec = speech.StreamDecoder(decode=lambda b: (np.ones(len(b) * 10, np.float32), 44100.0))
    assert dec.feed(b"x" * 100) == []  # 还不够第一次解码
    got = dec.feed(b"x" * 1500)
    assert len(got[0][0]) == 16000 - speech.MARGIN
    rest = dec.finish()
    assert len(rest[0][0]) == speech.MARGIN


# ---------------- 旁白：只念当前这一步 ----------------
class FakePlayer:
    def __init__(self):
        self.pushed, self.cleared = [], 0

    def warmup(self, sr=44100): pass

    def push(self, x, sr, on_start=None):
        self.pushed.append(float(x[0]))
        if on_start:
            on_start()

    def clear(self): self.cleared += 1
    def playing(self): return False


class FakeOverlay:
    def show_caption(self, t): self.caption = t
    def clear_caption(self): self.caption = None


def _narrator(monkeypatch, gates):
    """每句话的语音流：等对应的 Event 放行后才给数据。数据第一个字节 = 这句话的编号，解码后采样值 = 编号。"""
    monkeypatch.setattr(speech, "decode_mp3", lambda b: (np.full(len(b) * 4, float(b[0]), np.float32), 44100.0))

    def stream(text, lang):
        k = int(text.split()[-1])
        gates[k].wait(5)
        yield bytes([k]) * 4000

    p = FakePlayer()
    n = narrator_mod.Narrator(FakeOverlay(), None, lambda: True, lambda: "en", player=p, stream=stream)
    return n, p


def _wait(cond, s=3):
    t = time.time() + s
    while time.time() < t:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_new_step_interrupts_and_late_fetch_never_plays(monkeypatch):
    gates = {1: threading.Event(), 2: threading.Event()}
    n, p = _narrator(monkeypatch, gates)
    n.say("step 1", interrupt=True, tag=1)
    first = n.current
    n.say("step 2", interrupt=True, tag=2)  # 新的一步：打断第 1 步（它的语音还在路上）
    assert first.dead and n.current.tag == 2
    gates[2].set()
    assert _wait(lambda: n.current.done)
    gates[1].set()  # 第 1 步的语音晚到
    assert _wait(lambda: first.done)
    assert p.pushed and set(p.pushed) == {2.0}  # 只放了第 2 步


def test_stop_drops_in_flight_speech(monkeypatch):
    gates = {7: threading.Event()}
    n, p = _narrator(monkeypatch, gates)
    n.say("step 7", tag=7)
    item = n.current
    n.stop()
    gates[7].set()
    assert _wait(lambda: item.done)
    assert p.pushed == [] and p.cleared >= 1 and n.current is None


def test_voice_start_callback_fires_once_per_sentence(monkeypatch):
    gates = {3: threading.Event()}
    n, p = _narrator(monkeypatch, gates)
    starts = []
    n.on_voice_start = starts.append
    gates[3].set()
    n.say("step 3", tag=3)
    assert _wait(lambda: n.current.done)
    assert starts == [3]


# ---------------- 两个声音不重叠 ----------------
class FakeEngine:
    def __init__(self): self.played, self.cleared, self._q = [], 0, False
    def play(self, b): self.played.append(b); self._q = True
    def clear(self): self.cleared += 1; self._q = False
    def set_volume(self, v): pass
    def playing(self): return self._q


def _agent_audio():
    a = FilteredAgentAudio(Gate(voiceprint=None, log=lambda *x: None))
    a.engine = FakeEngine()
    return a


def test_client_step_clears_agent_audio_and_drops_rest_of_that_response():
    a = _agent_audio()
    starts = []
    a.on_voice_start = starts.append
    a.output(b"sure-let-me-look")  # Agent 在说 "Sure, let me look."
    a.drop_for_client()  # 新的一步出框，客户端开始念
    assert a.engine.cleared == 1
    a.output(b"okay")  # 同一轮里还在路上的 / SHOWN 之后的 "Okay." → 丢掉
    assert a.engine.played == [b"sure-let-me-look"]
    a.new_user_turn()  # 用户按右 Option 问了个问题：回答要正常放
    a.output(b"answer")
    assert a.engine.played[-1] == b"answer" and not a.muted
    assert starts == [0, 1]  # 每段新话的 gen：第二段是在 drop_for_client 之后


def test_allow_agent_keeps_mute_state():
    a = _agent_audio()
    a.drop_for_client()
    a.silence_now()  # 用户正在说话
    a.allow_agent()
    a.output(b"x")
    assert a.engine.played == []  # 还在静音（用户说话期间）
    a.new_user_turn()
    a.output(b"y")
    assert a.engine.played == [b"y"]


# ---------------- tool 返回值 ----------------
def test_tool_reply_prefixes():
    assert tool_reply({"status": "next", "box": [1, 2, 3, 4], "instruction": "Click Clone"}) == "SHOWN: Click Clone"
    assert tool_reply({"status": "done", "instruction": "All set"}) == "DONE: All set"
    assert tool_reply({"status": "unsure", "instruction": "Open VS Code"}) == "UNSURE: Open VS Code"
    assert tool_reply({"status": "next", "box": None, "instruction": "?"}).startswith("UNSURE: ")


# ---------------- 出框 = 念 + [step] 背景消息 ----------------
class FakeAgentSession:
    active = True

    def __init__(self):
        self.audio = _agent_audio()
        self.sent, self.ctx = [], []

    def send(self, t): self.sent.append(t); return True
    def context(self, t): self.ctx.append(t); return True


class FakeNarrator:
    def __init__(self): self.said, self.stopped = [], 0
    def say(self, text, interrupt=False, tag=None): self.said.append((text, interrupt, tag))
    def stop(self): self.stopped += 1
    def speaking(self): return bool(self.said)


def _fake_controller():
    c = types.SimpleNamespace(agent=FakeAgentSession(), narrator=FakeNarrator(), _talking=False,
                              _speak_after_reply=False, history=[{"instruction": "a"}], _sync_step=None, shown=[])

    def show(box, text, kind):
        c.shown.append((box, text, kind))
        c._sync_step = len(c.history) + 1

    c._show_step = show
    for name in ("_present_step", "_speak_step", "_on_agent_voice_start", "_let_agent_speak"):
        setattr(c, name, types.MethodType(getattr(Controller, name), c))
    return c


def test_present_step_speaks_and_sends_contextual_update_not_screen_changed():
    c = _fake_controller()
    c.agent.audio.output(b"sure")  # Agent 正在说
    c._present_step([1, 2, 3, 4], "Click Clone Repository", "ocr")
    assert c.narrator.said == [("Click Clone Repository", True, 2)]
    assert c.agent.ctx == ["[step] Step 2 shown: Click Clone Repository"]
    assert c.agent.sent == []  # 不再发 [screen changed] 用户消息
    assert c.agent.audio.engine.cleared == 1 and c.agent.audio.drop_stale
    c._present_step([1, 2, 3, 5], "Click Clone from URL", "correction")  # 更正：再念一次（打断）
    assert c.narrator.said[-1] == ("Click Clone from URL", True, 2)
    assert c.agent.ctx[-1] == "[step] Step 2 shown: Click Clone from URL"


def test_no_step_speech_while_user_is_talking():
    c = _fake_controller()
    c._talking = True
    c._present_step([1, 2, 3, 4], "Click Clone", "gemini")
    assert c.narrator.said == [] and c.narrator.stopped == 1 and c.shown


def test_agent_answer_stops_client_step_but_stale_callback_does_not():
    c = _fake_controller()
    c.task = {"goal": "x"}
    c._present_step([1, 2, 3, 4], "Click Clone", "gemini")
    gen = c.agent.audio.drop_gen
    c._on_agent_voice_start(gen - 1)  # 这一步出框之前就开口的那段：不停新的一步
    assert c.narrator.stopped == 0
    c._on_agent_voice_start(gen)  # 用户问了问题，Agent 在回答 → 停掉客户端的步骤
    assert c.narrator.stopped == 1


def test_tool_done_lets_agent_speak():
    c = _fake_controller()
    c._present_step([1, 2, 3, 4], "Click Clone", "tool")
    c._let_agent_speak()
    c.agent.audio.output(b"great job")
    assert c.agent.audio.engine.played[-1] == b"great job" and c.narrator.stopped == 1


# ---------------- /api/speak 流式请求 ----------------
def _use(monkeypatch, handler):
    import httpx

    from showme import api
    monkeypatch.setattr(api, "_client", httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x"))
    return api


def test_speak_stream_sends_token_and_yields_chunks(monkeypatch):
    import httpx

    seen = {}

    def handler(r):
        seen["token"] = r.headers.get("X-ShowMe-Token")
        return httpx.Response(200, content=b"abc" * 1000, headers={"content-type": "audio/mpeg"})

    api = _use(monkeypatch, handler)
    monkeypatch.setattr(api, "API_TOKEN", "secret")
    assert b"".join(api.speak_stream("hi", "en")) == b"abc" * 1000
    assert seen["token"] == "secret"


def test_speak_stream_204_and_401(monkeypatch):
    import httpx

    from showme import i18n
    i18n.set_lang("en")
    api = _use(monkeypatch, lambda r: httpx.Response(204))
    assert list(api.speak_stream("hi", "en")) == []
    api = _use(monkeypatch, lambda r: httpx.Response(401, json={"error": "口令不对"}))
    with pytest.raises(api.ApiError, match="Wrong access token — check SHOWME_TOKEN"):
        list(api.speak_stream("hi", "en"))


def test_player_fill_marks_start_and_clears():
    p = speech.PcmPlayer()  # 不打开输出流：直接调回调
    started = []
    p._chunks.append([np.ones(300, np.float32), 0, lambda: started.append(1)])
    p._chunks.append([np.full(300, 2, np.float32), 0, None])
    out = np.zeros((512, 1), np.float32)
    p._fill(out, 512, None, None)
    assert started == [1] and out[0, 0] == 1 and out[400, 0] == 2 and p.playing()
    p.clear()
    p._fill(out, 512, None, None)
    assert not p.playing() and out.max() == 0
