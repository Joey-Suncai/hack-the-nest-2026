"""框和声音的同步计时（一直开着，联调对比用）。

同一个单调时钟（进程启动后的秒数），每一步打两行：
  [sync] step 3 box shown t=12.345 kind=ocr text='Click Clone'
  [sync] step 3 voice start t=12.789 src=client box→voice=+0.444s
算「框 → 声音」延迟：直接看 box→voice，或者用两行的 t 相减。
其他：
  [sync] step 3 agent voice start t=... (agent 自己开口：回答问题 / 念步骤)
  [sync] step 3 agent called show_next_step by itself t=... （这一步已经显示着、用户没开口）
"""

import threading
import time

_T0 = time.monotonic()
_lock = threading.Lock()
_box_t: dict[int, float] = {}  # 第几步 → 最近一次出框时刻
_voiced: set[int] = set()  # 这一步（这一次出框之后）已经记过声音开始


def now() -> float:
    return time.monotonic() - _T0


def box(step: int, kind: str, text: str) -> None:
    t = now()
    with _lock:
        _box_t[step] = t
        _voiced.discard(step)
    print(f"[sync] step {step} box shown t={t:.3f} kind={kind} text={text!r}", flush=True)


def voice(step: int | None, src: str) -> None:
    """src：client（客户端自己念）/ agent（Agent 的声音）。同一次出框只记第一次。"""
    t = now()
    if step is None:
        print(f"[sync] voice start t={t:.3f} src={src} (not a step)", flush=True)
        return
    with _lock:
        bt = _box_t.get(step)
        first = step not in _voiced
        _voiced.add(step)
    delay = f" box→voice=+{t - bt:.3f}s" if bt is not None and first else ""
    print(f"[sync] step {step} voice start t={t:.3f} src={src}{delay}", flush=True)


def log(msg: str) -> None:
    print(f"[sync] {msg} t={now():.3f}", flush=True)
