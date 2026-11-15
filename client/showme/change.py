"""画面变化检测：判断用户是不是做完了这一步。"""

import numpy as np

from showme.config import (CHANGE_THRESHOLD, PIXEL_DELTA, RECHECK_AX_MIN, RECHECK_AX_RATIO, RECHECK_CHANGE,
                           RECHECK_PIXEL_DELTA, STABLE_FRAMES)


# ---- 复查用（操作后 1.5 s 只比一次，可以比平时的轮询灵敏）----
def fine_diff(a: np.ndarray, b: np.ndarray) -> float:
    """比 _diff 灵敏：深色模式里深灰弹窗盖在深灰窗口上，亮度变化不到 PIXEL_DELTA（日志：diff=0.0039，差一点没过）。"""
    return float((np.abs(a - b) > RECHECK_PIXEL_DELTA).mean())


def ax_signature(elements) -> frozenset:
    """控件清单的「指纹」：(角色, 名字)。弹窗开 / 关、选中状态（名字里常带 'selected'）变了都会反映出来；不看位置。"""
    try:
        return frozenset((e.get("role"), e.get("name")) for e in (elements or []) if isinstance(e, dict))
    except Exception:  # noqa: BLE001
        return frozenset()


def ax_changed(before: frozenset | None, after: frozenset | None) -> bool:
    """控件明显变了：至少 RECHECK_AX_MIN 个不同，而且占总数 RECHECK_AX_RATIO 以上。读不到（None / 空）就不下结论。"""
    if not before or not after:
        return False
    diff = len(before ^ after)
    return diff >= RECHECK_AX_MIN and diff / max(1, len(before | after)) >= RECHECK_AX_RATIO


def screen_changed(pixel_diff: float, ax_before: frozenset | None, ax_after: frozenset | None) -> bool:
    return pixel_diff > RECHECK_CHANGE or ax_changed(ax_before, ax_after)


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    """明显变了的像素占多少比例。

    不用整屏平均差：弹出一个小菜单只占屏幕几个百分点，平均下来太小，会漏检。
    """
    return float((np.abs(a - b) > PIXEL_DELTA).mean())


class ChangeDetector:
    """和基准帧差得够多 = 画面变了；变了之后连续 stable_frames 帧不再动 → 返回一次 True。"""

    def __init__(self, threshold: float = CHANGE_THRESHOLD, stable_frames: int = STABLE_FRAMES):
        self.threshold = threshold
        self.stable_frames = stable_frames
        self._base: np.ndarray | None = None

    def reset(self, baseline: np.ndarray) -> None:
        self.last_diff = 0.0  # 调试用：最近一次和基准帧（或上一帧）的差
        self._base = baseline
        self._prev: np.ndarray | None = None
        self._changed = False
        self._stable = 0
        self._fired = False

    def feed(self, frame: np.ndarray) -> bool:
        if self._base is None or self._fired:
            return False
        if not self._changed:
            self.last_diff = _diff(frame, self._base)
            if self.last_diff > self.threshold:
                self._changed = True
                self._prev = frame
            return False
        self.last_diff = _diff(frame, self._prev)
        if self.last_diff <= self.threshold:
            self._stable += 1
        else:
            self._stable = 0
        self._prev = frame
        if self._stable >= self.stable_frames:
            self._fired = True
            return True
        return False


def mask_rects(frame: np.ndarray, rects, screen_w: float, screen_h: float) -> np.ndarray:
    """把高亮层、面板所在区域涂成 0，避免它们自己的动画 / 文字被当成画面变化。

    rects: [(x, y, w, h), ...]，逻辑坐标。
    """
    out = frame.copy()
    h, w = out.shape
    for x, y, rw, rh in rects:
        x0 = max(0, int(x / screen_w * w))
        y0 = max(0, int(y / screen_h * h))
        x1 = min(w, int((x + rw) / screen_w * w) + 1)
        y1 = min(h, int((y + rh) / screen_h * h) + 1)
        out[y0:y1, x0:x1] = 0
    return out
