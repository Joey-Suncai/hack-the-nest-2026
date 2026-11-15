"""录入声纹：念 3 段话，每段 5 秒 → 3 个声纹取平均 → ~/.showme/voiceprints/<名字>.npy（只存在本机，不上传、不进 git）。
每个人录一次（Teresa、Joey），比对时谁的分数过阈值都算。

录音走和实时对话一样的链路（AVAudioEngine 语音处理），保证录入和比对的条件一致。
面板的「录入我的声音」按钮和命令行都用这里：
  cd client && uv run python -m showme.enroll teresa
  cd client && uv run python -m showme.enroll joey
"""

import subprocess
import sys
import time

import numpy as np

from showme import voice_filter as vf

SENTENCES = [
    "Hi ShowMe, I'm learning how to use a new app on my computer today.",
    "Please help me clone this repository and open it in Visual Studio Code.",
    "Wait, what does this button do? Can you show me the next step again?",
]
CLIP_S = 5.0


def level_db(x: np.ndarray) -> float:
    """有声部分的音量（dBFS），只算最响的 30% 的 32 ms 帧，避开停顿。"""
    frames = x[: len(x) // vf.FRAME * vf.FRAME].reshape(-1, vf.FRAME)
    if not len(frames):
        return -120.0
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    loud = np.sort(rms)[int(len(rms) * 0.7):]
    return float(20 * np.log10(np.median(loud)))


def loudest_part(x: np.ndarray, keep: float = 0.5) -> np.ndarray:
    """只留最响的那一半 32 ms 帧（按原顺序拼起来）。

    现场很吵时，5 秒录音里会混进旁边的人声；用户靠近麦克风说话的部分最响，只用这部分算声纹。
    """
    frames = x[: len(x) // vf.FRAME * vf.FRAME].reshape(-1, vf.FRAME)
    if len(frames) < 4:
        return x
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return frames[rms >= np.quantile(rms, 1 - keep)].reshape(-1)


MIN_LEVEL_DB = -40.0  # 一段录音的音量低于这个，说明没录到人说话（没开口 / 开口太晚 / 离太远）
MAX_RETRY = 2


class NoVoiceError(RuntimeError):
    pass


def enroll(record, embed, on_prompt=print, sentences=SENTENCES, clip_s=CLIP_S, on_retry=None):
    """record(seconds) → 声音；embed(声音) → 声纹。返回 (平均声纹, 每段音量 dB 列表)。

    每段录完先看音量：太小就让用户再念一次（最多 MAX_RETRY 次），避免把一段空录音混进声纹。
    """
    prints, levels = [], []
    for i, s in enumerate(sentences, 1):
        for attempt in range(MAX_RETRY + 1):
            on_prompt(i, len(sentences), s)
            clip = record(clip_s)
            lvl = level_db(clip)
            if lvl >= MIN_LEVEL_DB:
                break
            if on_retry:
                on_retry(i, lvl)
        else:
            raise NoVoiceError(f"第 {i} 段一直没录到声音（{lvl:.1f} dBFS）：请靠近一点、听到提示音马上开口")
        prints.append(embed(loudest_part(clip)))
        levels.append(lvl)
    return vf.average_voiceprint(prints), levels


def save_named(name: str, vp: np.ndarray) -> "vf.Path":
    path = vf.VOICEPRINTS / f"{safe_name(name)}.npy"
    vf.save_voiceprint(vp, path)
    return path


def safe_name(name: str) -> str:
    s = "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch in "-_")
    return s or "me"


def main():
    from showme.audio_engine import record_clip

    name = sys.argv[1] if len(sys.argv) > 1 else "me"

    model = vf.SpeakerModel()

    def prompt(i, n, s):
        print(f"\n[{i}/{n}] 听到「叮」之后，用平时说话的音量念：\n    {s}", flush=True)
        subprocess.run(["afplay", "/System/Library/Sounds/Tink.aiff"])
        time.sleep(0.2)

    def retry(i, lvl):
        print(f"    ⚠️ 没听到你说话（{lvl:.1f} dBFS），请再念一次第 {i} 句", flush=True)
        subprocess.run(["say", "I didn't hear you. Please read it again."])

    try:
        vp, levels = enroll(record_clip, model.embed, prompt, on_retry=retry)
    except NoVoiceError as e:
        print(f"\n❌ {e}。声纹没有保存，原来的不变。", flush=True)
        return 1
    path = save_named(name, vp)
    print(f"\n已保存 {name} 的声纹到 {path}（只在本机）；现在录过的人：{list(vf.load_voiceprints())}")
    print(f"你说话的音量：{', '.join(f'{x:.1f}' for x in levels)} dBFS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
