"""录音：16 kHz 单声道，存成 WAV（发给 /api/transcribe）。

sounddevice 在它自己的音频线程里回调，start / stop 都不阻塞界面。
第一次用要给启动 ShowMe 的程序（VS Code / 终端）开「麦克风」权限。
"""

import io
import threading
import wave

import numpy as np

from showme.config import MIC_RATE


def to_wav(samples: np.ndarray, rate: int = MIC_RATE) -> bytes:
    """int16 单声道采样 → WAV 字节。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


class Recorder:
    def __init__(self):
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        """开始录音；没有麦克风 / 没有权限会抛异常，调用方负责提示用户。"""
        import sounddevice as sd  # 用到才导入：没装 PortAudio 也不影响打字模式

        self._chunks = []
        self._stream = sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="int16", callback=self._on_audio)
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status):
        with self._lock:
            self._chunks.append(indata[:, 0].copy())

    def stop(self) -> bytes:
        """结束录音，返回 WAV。"""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:  # noqa: BLE001
                print(f"[mic] 关闭录音失败：{e}")
        with self._lock:
            samples = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.int16)
            self._chunks = []
        return to_wav(samples)

    def seconds(self) -> float:
        with self._lock:
            return sum(len(c) for c in self._chunks) / MIC_RATE
