"""实时语音的音频接口（替换 ElevenLabs SDK 默认的 pyaudio 录放）。

麦克风 → macOS 语音处理（回声消除 + 降噪）→ 每 32 ms 一帧 → Silero VAD → Gate（先压低音量、再确认 / 声纹）→ Agent

- 录音和播放走同一个 AVAudioEngine：系统知道哪些是扬声器的声音，才能消掉
- AVAudioEngine 起不来（没权限 / 设备不支持）→ 退回 sounddevice（没有回声消除，VAD + 声纹照常）
- AudioInterface 的 4 个方法以 SDK 源码为准（elevenlabs 2.78.0 conversation.py）：
  start(input_callback) / stop() / output(audio) / interrupt()，音频都是 16 kHz 16-bit 单声道 PCM
"""

import queue
import threading
import time

import numpy as np
from elevenlabs.conversational_ai.conversation import AudioInterface

from showme import config as C
from showme.voice_filter import FRAME, RATE, Gate, SileroVAD


def resample_to_16k(x: np.ndarray, sr: float) -> np.ndarray:
    if sr == RATE:
        return x
    if sr == 48000 and len(x) % 3 == 0:
        return x.reshape(-1, 3).mean(axis=1)  # 3 点平均 = 简单低通 + 抽取，够语音用
    n = int(round(len(x) * RATE / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


# ================= 录放引擎 =================
class AVEngine:
    """AVAudioEngine：输入开语音处理（回声消除），播放走同一个引擎的 player 节点。"""

    name = "AVAudioEngine + 回声消除"

    def __init__(self):
        import AVFoundation as AV

        self.AV = AV
        self.engine = AV.AVAudioEngine.alloc().init()
        self.engine.mainMixerNode()
        self.engine.outputNode()  # 先建好混音器 / 输出节点再开语音处理，否则启动报 -10875
        inp = self.engine.inputNode()
        ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
        self.voice_processing = bool(ok)
        if not ok:
            print(f"[audio] 打不开语音处理（回声消除）：{err}", flush=True)
        self.player = AV.AVAudioPlayerNode.alloc().init()
        self.engine.attachNode_(self.player)
        self.play_fmt = AV.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(RATE, 1)
        self.engine.connect_to_format_(self.player, self.engine.mainMixerNode(), self.play_fmt)
        self._pending = 0
        self._lock = threading.Lock()

    def start(self, on_audio):
        """on_audio(float32 16 kHz 单声道)，在音频线程里调用，要快。"""
        inp = self.engine.inputNode()
        fmt = inp.outputFormatForBus_(0)
        sr = fmt.sampleRate()

        def tap(buf, when):
            n = buf.frameLength()
            ch0 = np.frombuffer(buf.floatChannelData()[0].as_buffer(n), dtype=np.float32, count=n).copy()
            on_audio(resample_to_16k(ch0, sr))

        inp.installTapOnBus_bufferSize_format_block_(0, 4800, fmt, tap)
        self.engine.prepare()
        ok, err = self.engine.startAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"AVAudioEngine 启动失败：{err}")
        self.player.play()

    def stop(self):
        try:
            self.engine.inputNode().removeTapOnBus_(0)
            self.player.stop()
            self.engine.stop()
        except Exception as e:  # noqa: BLE001
            print(f"[audio] 停止引擎出错：{e}", flush=True)

    def play(self, pcm16: bytes):
        x = np.frombuffer(pcm16, np.int16).astype(np.float32) / 32768
        if not len(x):
            return
        buf = self.AV.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(self.play_fmt, len(x))
        buf.setFrameLength_(len(x))
        np.frombuffer(buf.floatChannelData()[0].as_buffer(len(x)), dtype=np.float32, count=len(x))[:] = x
        with self._lock:
            self._pending += 1

        def done():
            with self._lock:
                self._pending = max(0, self._pending - 1)

        self.player.scheduleBuffer_completionHandler_(buf, done)

    def clear(self):
        self.player.stop()  # 清掉已经排队的声音
        with self._lock:
            self._pending = 0
        self.player.play()

    def set_volume(self, v: float):
        self.player.setVolume_(v)

    def playing(self) -> bool:
        with self._lock:
            return self._pending > 0


class SDEngine:
    """退路：sounddevice（没有回声消除）。"""

    name = "sounddevice（没有回声消除）"
    voice_processing = False

    def __init__(self):
        import sounddevice as sd

        self.sd = sd
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._buf = np.zeros(0, np.float32)
        self._vol = 1.0

    def start(self, on_audio):
        self._in = self.sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=1600,
                                       callback=lambda d, f, t, s: on_audio(d[:, 0].copy()))
        self._out = self.sd.OutputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=800, callback=self._fill)
        self._in.start()
        self._out.start()

    def _fill(self, out, frames, t, status):
        while len(self._buf) < frames and not self._q.empty():
            self._buf = np.concatenate([self._buf, self._q.get_nowait()])
        chunk, self._buf = self._buf[:frames], self._buf[frames:]
        out[:len(chunk), 0] = chunk * self._vol
        out[len(chunk):, 0] = 0

    def stop(self):
        for s in (getattr(self, "_in", None), getattr(self, "_out", None)):
            try:
                s.stop()
                s.close()
            except Exception:  # noqa: BLE001
                pass

    def play(self, pcm16: bytes):
        self._q.put(np.frombuffer(pcm16, np.int16).astype(np.float32) / 32768)

    def clear(self):
        self._buf = np.zeros(0, np.float32)
        with self._q.mutex:
            self._q.queue.clear()

    def set_volume(self, v: float):
        self._vol = v

    def playing(self) -> bool:
        return len(self._buf) > 0 or not self._q.empty()


def open_engine():
    """优先 AVAudioEngine（有回声消除），不行就 sounddevice。"""
    try:
        return AVEngine()
    except Exception as e:  # noqa: BLE001
        print(f"[audio] AVAudioEngine 用不了，退回 sounddevice：{e}", flush=True)
        return SDEngine()


# ================= 给 ElevenLabs Agent 用的音频接口 =================
class FilteredAgentAudio(AudioInterface):
    """把过滤后的麦克风声音交给 Agent；Agent 的声音从同一个引擎播放。"""

    def __init__(self, gate: Gate, on_event=None, use_aec: bool | None = None):
        self.gate = gate
        self.use_aec = C.USE_AEC if use_aec is None else use_aec
        self.on_event = on_event or (lambda kind, info: None)  # 给面板 / 日志：("accept"|"reject"|"duck", info)
        self.stats = {"interrupts": 0, "rejected": 0, "accepted_while_quiet": 0}
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._running = False
        self.engine = None  # start() 里才建（SDK 在它自己的线程里调 start）
        self.started = threading.Event()
        self.muted = False  # 用户在说话 / 按了 Stop：丢掉 Agent 发过来的声音
        self.sent_voice_frames = 0  # 调试：这次收音实际发出去多少帧非静音声音
        self.sent_voice_sumsq = 0.0
        self.on_first_audio = None  # 计时：用户说完后 Agent 的第一段声音开始播放时回调（音频线程里）
        self.await_first_audio = False
        self.on_voice_start = None  # (gen) Agent 开始说一段新的话（播放队列本来是空的）时回调（SDK 线程里）
        # 两个声音不重叠：客户端开始念一步 → 清掉 Agent 正在放的声音，并丢掉它之后还在路上的声音，
        # 直到用户下一次开口 / 打字（new_user_turn）。这段时间 Agent 本来就不该说话（SHOWN 之后最多一句 "Okay."）
        self.drop_stale = False
        self.drop_gen = 0  # 每次 drop_for_client +1：Agent 开口的回调带上它，晚到的回调不会误停新的一步
        self._out_lock = threading.Lock()
        self._dropped = 0

    # ---- SDK 要求的 4 个方法 ----
    def start(self, input_callback):
        self._send = input_callback
        self.vad = SileroVAD()
        self.engine = open_engine() if self.use_aec else SDEngine()
        self._running = True
        self._worker = threading.Thread(target=self._loop, name="voice-filter", daemon=True)
        self._worker.start()
        try:
            self.engine.start(self._q.put)
        except Exception as e:  # noqa: BLE001
            print(f"[audio] {self.engine.name} 启动失败，退回 sounddevice：{e}", flush=True)
            self.engine = SDEngine()
            self.engine.start(self._q.put)
        print(f"[audio] 录放：{self.engine.name}", flush=True)
        self.started.set()

    def stop(self):
        self._running = False
        if self.engine:
            self.engine.stop()
        print(f"[voice] 本次统计：打断 {self.stats['interrupts']} 次，拒绝（不是本人 / 杂音）{self.stats['rejected']} 次，"
              f"ShowMe 没说话时发出的语音 {self.stats['accepted_while_quiet']} 段", flush=True)

    def output(self, audio: bytes):
        with self._out_lock:
            if not self.engine or self.muted:
                return
            if self.drop_stale:
                self._dropped += 1
                if self._dropped == 1:
                    print("[sync] agent audio dropped (client is reading a step; waiting for the user's next turn)", flush=True)
                return
            if self.await_first_audio:
                self.await_first_audio = False
                if self.on_first_audio:
                    self.on_first_audio()
            if not self.engine.playing() and self.on_voice_start:
                self.on_voice_start(self.drop_gen)
            self.engine.play(audio)

    def drop_for_client(self):
        """客户端要念一步了：清掉 Agent 正在放的，之后到的也丢掉，直到 new_user_turn()。"""
        with self._out_lock:
            self.drop_stale = True
            self.drop_gen += 1
            self._dropped = 0
            if self.engine:
                was = self.engine.playing()
                self.engine.clear()
                self.engine.set_volume(1.0)
                if was:
                    print("[sync] agent audio cleared (client step starts)", flush=True)

    def new_user_turn(self):
        """用户刚说完 / 打了字：Agent 接下来的回答要放出来。"""
        with self._out_lock:
            if self.drop_stale and self._dropped:
                print(f"[sync] dropped {self._dropped} agent audio chunks since the last step", flush=True)
            self.drop_stale = False
            self.muted = False

    def allow_agent(self):
        """Agent 要说的话（tool 返回 DONE / UNSURE，它负责转告）：不再丢它的声音（静音状态不变）。"""
        with self._out_lock:
            self.drop_stale = False

    def silence_now(self):
        """Stop：立刻清空播放队列，并丢掉之后还在路上的声音。"""
        with self._out_lock:
            self.muted = True
            if self.engine:
                self.engine.clear()
                self.engine.set_volume(1.0)

    def interrupt(self):
        if self.engine:
            self.engine.clear()
            self.engine.set_volume(1.0)

    # ---- 处理线程：凑够 512 个采样 → VAD → Gate ----
    def _loop(self):
        pending = np.zeros(0, np.float32)
        silence = np.zeros(FRAME, np.int16).tobytes()
        while self._running:
            try:
                pending = np.concatenate([pending, self._q.get(timeout=0.2)])
            except queue.Empty:
                continue
            while len(pending) >= FRAME:
                frame, pending = pending[:FRAME], pending[FRAME:]
                try:
                    self._handle(frame, silence)
                except Exception as e:  # noqa: BLE001  一帧出错不能把整条链路弄停
                    print(f"[voice] 处理出错：{e}", flush=True)

    def _handle(self, frame: np.ndarray, silence: bytes):
        speaking = self.engine.playing()
        out = self.gate.feed(frame, self.vad.prob(frame), speaking)
        if out.duck is True:
            self.engine.set_volume(C.DUCK_VOLUME)
            self.on_event("duck", None)
        elif out.duck is False:
            self.engine.set_volume(1.0)
        if out.decision == "accept":
            if out.stop_playback:
                self.engine.clear()
                self.stats["interrupts"] += 1
                print(f"[voice] 打断 #{self.stats['interrupts']}（分数 {out.score if out.score is not None else '-'}）", flush=True)
            else:
                self.stats["accepted_while_quiet"] += 1
            self.on_event("accept", out.score)
        elif out.decision == "reject":
            self.stats["rejected"] += 1
            print(f"[voice] 拒绝 #{self.stats['rejected']}（分数 {out.score:.3f}）", flush=True)
            self.on_event("reject", out.score)
        if out.send:
            chunk = np.concatenate(out.send)
            self.sent_voice_frames += len(out.send)
            self.sent_voice_sumsq += float((chunk ** 2).sum())
            self._send((np.clip(chunk, -1, 1) * 32767).astype(np.int16).tobytes())
        else:
            self._send(silence)  # 不发声音时发静音帧，保持连接


# ================= 录入声纹 =================
def record_clip(seconds: float, engine=None) -> np.ndarray:
    """用和实时对话同样的录音链路（有回声消除 / 降噪）录一段，保证声纹录入和比对条件一致。"""
    eng = engine or open_engine()
    chunks: list[np.ndarray] = []
    try:
        eng.start(chunks.append)
    except Exception:  # noqa: BLE001
        eng = SDEngine()
        eng.start(chunks.append)
    t_end = time.time() + seconds
    while time.time() < t_end:
        _pump(0.05)
    eng.stop()
    return np.concatenate(chunks)[: int(seconds * RATE)] if chunks else np.zeros(0, np.float32)


def _pump(sec: float):
    """AVAudioEngine 的回调要靠 run loop；在 Qt 里会有 Qt 的事件循环，这里给命令行 / 测试用。"""
    try:
        from Foundation import NSDate, NSRunLoop

        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(sec))
    except Exception:  # noqa: BLE001
        time.sleep(sec)
