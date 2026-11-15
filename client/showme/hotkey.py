"""按键说话：单独按一下右 Option（按下再松开）= 切换收音开 / 关。

- 只有「单独按右 Option 再松开」才算：按住期间按了别的键（Option+字母打特殊符号）或别的修饰键，都不算
- NSEvent 全局监听（用户在 VS Code 等别的 App 里也能触发）+ 本地监听（ShowMe 自己在前面时）
- 全局监听要「辅助功能」权限：没有时弹系统授权提示，面板提示用户；没权限时麦克风按钮照样能用
"""

# NSEvent 修饰键（AppKit）和设备相关位（IOKit NX_DEVICE*）
RIGHT_OPTION_KEYCODE = 61
FLAG_OPTION = 1 << 19
FLAG_SHIFT = 1 << 17
FLAG_CONTROL = 1 << 18
FLAG_COMMAND = 1 << 20
FLAG_FUNCTION = 1 << 23
DEVICE_LEFT_OPTION = 0x20
DEVICE_RIGHT_OPTION = 0x40
OTHER_MODIFIERS = FLAG_SHIFT | FLAG_CONTROL | FLAG_COMMAND | FLAG_FUNCTION


class RightOptionTap:
    """纯逻辑（不碰系统），方便测试。flags_changed / key_down 返回 True = 这一次算「按了一下」。"""

    def __init__(self):
        self._down = False
        self._dirty = False

    def flags_changed(self, keycode: int, flags: int) -> bool:
        right_down = bool(flags & DEVICE_RIGHT_OPTION)
        if keycode == RIGHT_OPTION_KEYCODE:
            if right_down and not self._down:  # 按下
                self._down = True
                # 按下时已经按着别的修饰键（或左 Option）→ 这次不算
                self._dirty = bool(flags & OTHER_MODIFIERS) or bool(flags & DEVICE_LEFT_OPTION)
                return False
            if not right_down and self._down:  # 松开：干净的一下才切换
                self._down = False
                return not self._dirty
            return False
        if self._down:  # 按着右 Option 时又按了 / 松了别的修饰键
            self._dirty = True
        return False

    def key_down(self) -> bool:
        if self._down:  # 按着右 Option 时打了字（比如 Option+e）
            self._dirty = True
        return False


def accessibility_ok(prompt: bool = True) -> bool:
    """有没有「辅助功能」权限；没有且 prompt=True 时弹出系统授权提示。出错当成没有。"""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    except Exception as e:  # noqa: BLE001
        print(f"[hotkey] 检查辅助功能权限失败：{e}", flush=True)
        return False


class RightOptionMonitor:
    """装上 NSEvent 全局 + 本地监听；单独按一下右 Option 就调用 on_tap()（在主线程）。"""

    def __init__(self, on_tap):
        self.on_tap = on_tap
        self.tap = RightOptionTap()
        self._monitors = []

    def _handle(self, event):
        try:
            from AppKit import NSEventTypeFlagsChanged, NSEventTypeKeyDown

            et = event.type()
            if et == NSEventTypeFlagsChanged:
                fired = self.tap.flags_changed(int(event.keyCode()), int(event.modifierFlags()))
            elif et == NSEventTypeKeyDown:
                fired = self.tap.key_down()
            else:
                fired = False
            if fired:
                self.on_tap()
        except Exception as e:  # noqa: BLE001  监听里出错不能把程序弄崩
            print(f"[hotkey] 处理按键出错：{e}", flush=True)

    def start(self) -> bool:
        """返回全局监听有没有装上（没权限时只有本地监听：ShowMe 在前面时才能用）。"""
        from PyQt6.QtWidgets import QApplication

        if QApplication.platformName() != "cocoa":  # 离屏测试：不装、不弹授权窗口
            return True
        try:
            from AppKit import NSEvent, NSEventMaskFlagsChanged, NSEventMaskKeyDown

            mask = NSEventMaskFlagsChanged | NSEventMaskKeyDown

            def local(event):
                self._handle(event)
                return event  # 本地监听要把事件还回去

            self._monitors.append(NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local))
            trusted = accessibility_ok(prompt=True)
            g = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self._handle)
            if g is not None:
                self._monitors.append(g)
            print(f"[hotkey] 右 Option 按键说话：本地监听已装；全局监听 {'已装' if trusted and g else '没有辅助功能权限，只有 ShowMe 在前面时能用'}", flush=True)
            return bool(trusted and g)
        except Exception as e:  # noqa: BLE001
            print(f"[hotkey] 装不上按键监听：{e}", flush=True)
            return False

    def stop(self):
        try:
            from AppKit import NSEvent

            for m in self._monitors:
                NSEvent.removeMonitor_(m)
        except Exception:  # noqa: BLE001
            pass
        self._monitors = []
