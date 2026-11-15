"""边下载边播：/api/speak 的 mp3 流一边到一边解码成 PCM，马上开始放（不等整段下完）。

- 解码：macOS 自带的 AVAudioFile（PyObjC，不加新依赖）。它只能读文件，所以每来一批数据，
  就把「到目前为止的全部字节」写进临时文件重新解码一遍（一句话的 mp3 只有几十 KB，一次 1–4 ms）。
  实测前缀解码出来的采样和整段解码一模一样，只有最后一帧（≤ 1152 个采样）不同 → 每次少放最后 MARGIN 个，
  等后面的数据到了再补上；流结束时把剩下的全放完。
- 播放：sounddevice 输出流（和 Agent 的声音分开，系统混音），push / clear 线程安全。
- 解码一次都没成功（格式不认识）→ 调用方拿 data 退回 afplay 整段播放。
"""

import os
import tempfile
import threading
from collections import deque

import numpy as np

MARGIN = 2304  # 两帧：前缀解码最后一帧不完整，先不放
FIRST_BYTES = 1200  # 第一次尝试解码要攒多少字节（128 kbps 约 3 帧 ≈ 75 ms 声音）
STEP_BYTES = 3000  # 之后每多来这么多字节解码一次


def decode_mp3(data: bytes):
    """mp3 字节 → (float32 单声道, 采样率)；解不了返回 None。"""
    import AVFoundation as AV
    from Foundation import NSURL

    fd, path = tempfile.mkstemp(suffix=".mp3")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        f, _err = AV.AVAudioFile.alloc().initForReading_commonFormat_interleaved_error_(
            NSURL.fileURLWithPath_(path), AV.AVAudioPCMFormatFloat32, False, None)
        if f is None:
            return None
        fmt, n = f.processingFormat(), f.length()
        if n <= 0:
            return np.zeros(0, np.float32), fmt.sampleRate()
        buf = AV.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(fmt, n)
        ok, _err = f.readIntoBuffer_error_(buf, None)
        if not ok:
            return None
        m = buf.frameLength()
        x = np.frombuffer(buf.floatChannelData()[0].as_buffer(m), dtype=np.float32, count=m).copy()
        return x, float(fmt.sampleRate())
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class StreamDecoder:
    """feed(字节) → 新解出来、可以放的 [(pcm, sr)]；finish() → 剩下的。"""

    def __init__(self, decode=None):
        self.decode = decode or (lambda b: decode_mp3(b))
        self.data = bytearray()
        self.emitted = 0  # 已经交出去多少个采样
        self.decoded_ok = False
        self._last_try = 0

    def feed(self, chunk: bytes) -> list:
        self.data += chunk
        need = FIRST_BYTES if not self.decoded_ok else STEP_BYTES
        if len(self.data) - self._last_try < need:
            return []
        return self._emit(final=False)

    def finish(self) -> list:
        if not self.data:
            return []
        return self._emit(final=True)

    def _emit(self, final: bool) -> list:
        self._last_try = len(self.data)
        res = self.decode(bytes(self.data))
        if res is None:
            return []
        x, sr = res
        self.decoded_ok = True
        end = len(x) if final else max(self.emitted, len(x) - MARGIN)
        if end <= self.emitted:
            return []
        out = x[self.emitted:end]
        self.emitted = end
        return [(out, sr)]


class PcmPlayer:
    """单声道 PCM 播放队列。push 的第一段真正开始出声时调用 on_start（音频线程里）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._chunks: deque = deque()  # [array, offset, on_start]
        self._stream = None
        self._sr = None

    def warmup(self, sr: float = 44100):
        """提前打开输出流（打开要几十毫秒），放在等网络的时候做。"""
        with self._lock:
            self._ensure(sr)

    def _ensure(self, sr: float):
        if self._stream is not None and self._sr == sr:
            return
        import sounddevice as sd

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
        self._chunks.clear()
        self._sr = sr
        self._stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32", blocksize=512, callback=self._fill)
        self._stream.start()

    def push(self, x: np.ndarray, sr: float, on_start=None):
        with self._lock:
            self._ensure(sr)
            self._chunks.append([x, 0, on_start])

    def clear(self):
        with self._lock:
            self._chunks.clear()

    def playing(self) -> bool:
        with self._lock:
            return bool(self._chunks)

    def _fill(self, out, frames, t, status):
        starts = []
        with self._lock:
            i = 0
            while i < frames and self._chunks:
                c = self._chunks[0]
                x, off, cb = c
                if off == 0 and cb is not None:
                    starts.append(cb)
                    c[2] = None
                n = min(frames - i, len(x) - off)
                out[i:i + n, 0] = x[off:off + n]
                i += n
                c[1] += n
                if c[1] >= len(x):
                    self._chunks.popleft()
            out[i:, 0] = 0
        for cb in starts:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass


_player = None


def player() -> PcmPlayer:
    global _player
    if _player is None:
        _player = PcmPlayer()
    return _player
