"""判断「用户做完了这一步」的另外两种信号：鼠标点击、打字。

只靠画面变化不够：点输入框 / 勾选框 / 开关，变化只有一个光标或边框变色，而且正好在高亮框里，
64×64 缩略图里达不到阈值。所以：
- 点击：NSEvent 全局监听左键按下（用户点别的 App）；点在当前高亮框里（四周放宽）就算完成
- 打字：当前这一步是「输入内容」时，用户按回车，或者最后一次按键后 TYPE_IDLE_S 秒没再按 → 算完成
  隐私：只记「有按键、什么时候、是不是回车」，不记录、不打印按了什么

全局监听和右 Option 共用「辅助功能」权限；没权限时不装，退回只靠画面变化，不能崩。
"""

import re
import time

RETURN_KEYCODES = (36, 76)  # Return、小键盘 Enter

# 打字步骤：要用户输入内容（只认回车，或者用户说 "done"）
_TYPING_WORDS = re.compile(r"\b(type|typing|paste|write|fill in|enter (the|your|a|an)|press (enter|return))\b|输入|粘贴|填写|回车", re.IGNORECASE)
# 快捷键步骤：按任意键就算（"Press ⌘S"、"Press Tab"）
_KEY_WORDS = re.compile(r"\bpress\b|[⌘⌥⌃⇧]|\b(cmd|command|ctrl|control|shift|option|alt)\s*\+|按下?\s*(⌘|cmd|command|ctrl|shift|tab|esc)", re.IGNORECASE)


def to_top_left(x: float, y: float, screen_h: float) -> tuple[float, float]:
    """NSEvent.mouseLocation()：原点在主屏左下角、单位逻辑点 → 左上角原点（和高亮框 rect 一样）。"""
    return x, screen_h - y


def click_in_rect(pt: tuple[float, float], rect: tuple[int, int, int, int] | None, pad: float) -> bool:
    """点击是不是落在高亮框里（rect 四周再放宽 pad 个逻辑点）。"""
    if rect is None:
        return False
    x, y = pt
    rx, ry, rw, rh = rect
    return rx - pad <= x <= rx + rw + pad and ry - pad <= y <= ry + rh + pad


def step_kind(instruction: str | None, expect: str | None = None) -> str:
    """这一步靠什么信号算完成：
    - "type"：输入内容 → 只认回车（或用户说 done）。点击、画面变化都不算
    - "key"：按快捷键 → 有按键就算
    - "click"（默认）：点在框里；点在框外附近、而且点完画面明显变了，也算
    """
    text = instruction or ""
    if _TYPING_WORDS.search(text):
        return "type"
    if _KEY_WORDS.search(text):
        return "key"
    return "click"


def is_typing_step(instruction: str | None, expect: str | None = None, target_label: str | None = None) -> bool:
    return step_kind(instruction, expect) == "type"


def near_rect(pt: tuple[float, float], rect: tuple[int, int, int, int] | None, pad: float, near: float) -> bool:
    """点在框外，但离框不超过 near 个逻辑点（框画得偏一点时的后备）。"""
    return rect is not None and not click_in_rect(pt, rect, pad) and click_in_rect(pt, rect, pad + near)


class TypingTracker:
    """纯逻辑：记按键时间，判断打字是不是结束了。"""

    def __init__(self, idle_s: float):
        self.idle_s = idle_s
        self.reset()

    def reset(self):
        self.last_key: float | None = None
        self.enter = False

    def key(self, is_return: bool, now: float | None = None):
        self.last_key = time.monotonic() if now is None else now
        if is_return:
            self.enter = True

    def done(self, now: float | None = None) -> str | None:
        """'enter' / 'idle' / None。"""
        if self.enter:
            return "enter"
        now = time.monotonic() if now is None else now
        if self.last_key is not None and now - self.last_key >= self.idle_s:
            return "idle"
        return None


class InputWatcher:
    """装 NSEvent 监听：on_click(x, y)（左上角原点逻辑点，只有点别的 App 时）、on_key(is_return)。都在主线程回调。"""

    def __init__(self, on_click, on_key):
        self.on_click, self.on_key = on_click, on_key
        self._monitors = []

    def start(self) -> bool:
        from PyQt6.QtWidgets import QApplication

        if QApplication.platformName() != "cocoa":  # 离屏测试：不装
            return False
        try:
            from AppKit import NSEvent, NSEventMaskKeyDown, NSEventMaskLeftMouseDown, NSScreen

            from showme.hotkey import accessibility_ok

            if not accessibility_ok(prompt=False):
                print("[input] 没有辅助功能权限：不监听点击 / 打字，只靠画面变化判断", flush=True)
                return False
            screen_h = float(NSScreen.screens()[0].frame().size.height)  # 主屏（原点所在的那块）

            def click(event):
                try:
                    p = NSEvent.mouseLocation()
                    self.on_click(*to_top_left(float(p.x), float(p.y), screen_h))
                except Exception as e:  # noqa: BLE001
                    print(f"[input] 处理点击出错：{e}", flush=True)

            def key(event):
                try:
                    self.on_key(int(event.keyCode()) in RETURN_KEYCODES)  # 不读按键内容
                except Exception as e:  # noqa: BLE001
                    print(f"[input] 处理按键出错：{e}", flush=True)

            def local_click(event):
                return event  # 点 ShowMe 自己（面板）：不算完成步骤

            for m in (NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskLeftMouseDown, click),
                      NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskLeftMouseDown, local_click),
                      NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, key)):
                if m is not None:
                    self._monitors.append(m)
            print("[input] 已监听点击和打字（只记时间，不记内容）", flush=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[input] 装不上点击 / 打字监听：{e}", flush=True)
            return False

    def stop(self):
        try:
            from AppKit import NSEvent

            for m in self._monitors:
                NSEvent.removeMonitor_(m)
        except Exception:  # noqa: BLE001
            pass
        self._monitors = []
