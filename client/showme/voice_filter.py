"""实时语音的麦克风过滤：Silero VAD（人声检测）→ 声纹比对 → 决定要不要把声音发给 ElevenLabs Agent。

链路（在 audio_engine.py 里串起来）：
  麦克风 → macOS 语音处理（回声消除 + 降噪）→ 每 32 ms 一帧 → VAD → Gate（先压低播放音量、再确认）→ Agent

模型都在 ~/.showme/models/（不进 git），声纹在 ~/.showme/voiceprint.npy（只存在本机）。
Silero 写法以官方 utils_vad.py 的 OnnxWrapper 为准：16 kHz 每帧 512 个采样点，前面拼 64 个采样点的上下文。
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from showme import config as C

RATE = 16000
FRAME = 512  # Silero 16 kHz 只支持 512（32 ms）
FRAME_S = FRAME / RATE
HOME = Path(os.path.expanduser("~/.showme"))
MODELS = HOME / "models"
VAD_MODEL = MODELS / "silero_vad.onnx"
SPEAKER_MODEL = MODELS / "wespeaker_en_voxceleb_resnet34.onnx"  # 实测比 CAM++ 区分得开（同一声音 0.93 vs 不同 0.58）
VOICEPRINT = HOME / "voiceprint.npy"  # 旧的单人声纹（还认）
VOICEPRINTS = HOME / "voiceprints"  # 每个人一份：~/.showme/voiceprints/<名字>.npy


# ================= 人声检测 =================
class SileroVAD:
    """Silero VAD（ONNX，onnxruntime，不用 PyTorch）。prob(frame) → 0–1 的人声概率。"""

    CONTEXT = 64

    def __init__(self, path: Path = VAD_MODEL):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self._state = np.zeros((2, 1, 128), np.float32)
        self._context = np.zeros((1, self.CONTEXT), np.float32)

    def prob(self, frame: np.ndarray) -> float:
        """frame：512 个 float32 采样（-1..1）。"""
        x = np.concatenate([self._context, frame.reshape(1, FRAME).astype(np.float32)], axis=1)
        out, self._state = self.sess.run(None, {"input": x, "state": self._state, "sr": np.array(RATE, np.int64)})
        self._context = x[:, -self.CONTEXT:]
        return float(out[0][0])


# ================= 声纹 =================
class SpeakerModel:
    """WeSpeaker ResNet34（ONNX，sherpa-onnx 自带特征提取；CAM++ 实测分不开不同的人）。embed(声音) → 单位长度的声纹向量。"""

    def __init__(self, path: Path = SPEAKER_MODEL):
        import sherpa_onnx

        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=1)
        self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        st = self.ex.create_stream()
        st.accept_waveform(RATE, samples.astype(np.float32))
        st.input_finished()
        v = np.asarray(self.ex.compute(st), np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def average_voiceprint(embeddings: list[np.ndarray]) -> np.ndarray:
    v = np.mean(np.stack(embeddings), axis=0)
    return v / (np.linalg.norm(v) + 1e-9)


def save_voiceprint(v: np.ndarray, path: Path = VOICEPRINT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, v.astype(np.float32))


def load_voiceprint(path: Path = VOICEPRINT) -> np.ndarray | None:
    try:
        return np.load(path)
    except (OSError, ValueError):
        return None


def load_voiceprints(folder: Path = VOICEPRINTS, legacy: Path = VOICEPRINT) -> dict[str, np.ndarray]:
    """所有录过的人 {名字: 声纹}。"""
    out = {}
    if folder.is_dir():
        for f in sorted(folder.glob("*.npy")):
            v = load_voiceprint(f)
            if v is not None:
                out[f.stem] = v
    if not out and (v := load_voiceprint(legacy)) is not None:
        out["me"] = v
    return out


def best_score(emb: np.ndarray, prints: dict[str, np.ndarray]) -> tuple[str | None, float]:
    """和每个人比，取分数最高的那个人。"""
    if not prints:
        return None, -1.0
    name, v = max(prints.items(), key=lambda kv: cosine(emb, kv[1]))
    return name, cosine(emb, v)


# ================= 打断判断（先压低音量、再确认）=================
IDLE, CANDIDATE, ACCEPTED, REJECTED = "IDLE", "CANDIDATE", "ACCEPTED", "REJECTED"


@dataclass
class GateOut:
    send: list[np.ndarray] = field(default_factory=list)  # 这一帧要发给 Agent 的声音（可能包含缓存的好几帧）
    duck: bool | None = None  # True = 压低播放音量，False = 恢复，None = 不变
    stop_playback: bool = False  # 确认是本人：本地播放停掉（Agent 那边会自己打断）
    score: float | None = None  # 这一帧算了声纹时的分数（用来打日志）
    decision: str | None = None  # "accept" / "reject"


class Gate:
    """纯逻辑，不碰声卡，方便测试。每 32 ms 喂一帧：feed(frame, vad_prob, agent_speaking)。

    - 没有人声：什么都不发（调用方补静音帧，保持连接）
    - 检测到人声：ShowMe 正在说话就先压低音量；开始缓存，先不发
    - 累计 CONFIRM_S 秒人声后判断：
        有声纹且开了声纹过滤 → 余弦相似度 ≥ 阈值才算本人
        没声纹 / 关了声纹过滤 → 只要人声够长就算
      本人 → 把缓存和之后的声音都发出去，停掉本地播放；不是 → 恢复音量、丢掉缓存，这段话结束前都不发
    - 连续 END_S 秒没有人声 = 这段话结束，回到 IDLE
    - 按住说话模式：只有按着键时才发（不做 VAD / 声纹判断）
    """

    def __init__(self, scorer=None, voiceprint=None, threshold: float = C.VOICE_THRESHOLD,
                 vad_threshold: float = C.VAD_THRESHOLD, confirm_s: float = C.VOICE_CONFIRM_S, end_s: float = C.VOICE_END_S,
                 log=print):
        self.scorer = scorer  # samples → 声纹向量
        # 可以是一个人（向量）或几个人（{名字: 向量}）：Teresa 和 Joey 都会说话，谁的分数过阈值都算
        self.voiceprints = voiceprint if isinstance(voiceprint, dict) else ({"me": voiceprint} if voiceprint is not None else {})
        self.threshold = threshold
        self.vad_threshold = vad_threshold
        self.confirm_frames = max(1, round(confirm_s / FRAME_S))
        self.end_frames = max(1, round(end_s / FRAME_S))
        self.filter_enabled = True  # 面板「声纹过滤」
        self.push_to_talk = False  # 按键说话模式
        self.key_down = False
        self.open_at = 0.0  # 按键说话：这个时刻（time.monotonic）之前先不发——等提示音放完，免得被当成用户说的话
        self.log = log
        self._reset()

    def _reset(self):
        self.state = IDLE
        self._buf: list[np.ndarray] = []
        self._voiced = 0
        self._quiet = 0
        self._ducked = False

    @property
    def voiceprint(self):
        return next(iter(self.voiceprints.values()), None)

    @property
    def uses_voiceprint(self) -> bool:
        return self.filter_enabled and bool(self.voiceprints) and self.scorer is not None

    def feed(self, frame: np.ndarray, vad_prob: float, agent_speaking: bool) -> GateOut:
        out = GateOut()
        if self.push_to_talk:
            if self.key_down and time.monotonic() >= self.open_at:
                out.send.append(frame)
            return out
        speech = vad_prob >= self.vad_threshold
        self._quiet = 0 if speech else self._quiet + 1

        if self.state == IDLE:
            if speech:
                self.state = CANDIDATE
                self._buf, self._voiced = [frame], 1
                if agent_speaking:
                    out.duck, self._ducked = True, True
            return out

        if self.state == CANDIDATE:
            self._buf.append(frame)
            self._voiced += int(speech)
            if self._quiet >= self.end_frames:  # 话还没说够就停了：当成噪音
                if self._ducked:
                    out.duck = False
                self._reset()
                return out
            if self._voiced >= self.confirm_frames:
                self._decide(out)
            return out

        if self.state == ACCEPTED:
            out.send.append(frame)
            if self._quiet >= self.end_frames:
                self._reset()
            return out

        # REJECTED：这段话结束前都不发
        if self._quiet >= self.end_frames:
            self._reset()
        return out

    def _decide(self, out: GateOut):
        audio = np.concatenate(self._buf)
        if self.uses_voiceprint:
            t0 = time.perf_counter()
            who, score = best_score(self.scorer(audio), self.voiceprints)
            ok = score >= self.threshold
            out.score = score
            self.log(f"[voice] 声纹分数 {score:.3f}（最像 {who}，阈值 {self.threshold}）→ {'放行' if ok else '不是我们'}，"
                     f"用了 {(time.perf_counter() - t0) * 1000:.0f} ms")
        else:
            ok = True
            self.log(f"[voice] 人声持续 {len(self._buf) * FRAME_S:.2f}s → 发给 Agent（{'没录声纹' if self.voiceprint is None else '声纹过滤已关'}）")
        if self._ducked:
            out.duck = False
        if ok:
            out.decision, out.stop_playback = "accept", self._ducked
            out.send.extend(self._buf)
            self.state = ACCEPTED
        else:
            out.decision = "reject"
            self.state = REJECTED
        self._buf, self._ducked = [], False
