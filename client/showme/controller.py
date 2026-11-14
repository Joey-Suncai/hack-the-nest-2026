"""状态机：截图 → 问后端 → 画框 → 等画面变化 → 下一步。网络请求都在后台线程，界面不卡。

IDLE --start--> REQUESTING
REQUESTING ─ next   → 画框 → WAITING
           ─ done   → 清框，「完成！」→ IDLE
           ─ unsure → 清框，显示提示 → WAITING（画面变了或按「重试」再问）
           ─ 出错   → 面板显示错误 → PAUSED（按「重试」再问）
WAITING: 每 POLL_S 秒截图（不隐藏高亮层，遮掉框和面板）→ 画面变了且稳定 → 记进 history → REQUESTING
stop：任何状态 → 清框 → IDLE
"""

import traceback

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from showme import api, capture
from showme.change import ChangeDetector, mask_rects
from showme.config import HIGHLIGHT_PAD, POLL_S
from showme.coords import box_to_rect, expand

IDLE, REQUESTING, WAITING, PAUSED = "IDLE", "REQUESTING", "WAITING", "PAUSED"


class _Signals(QObject):
    ok = pyqtSignal(object)
    err = pyqtSignal(str)


class _Job(QRunnable):
    def __init__(self, fn, signals: _Signals):
        super().__init__()
        self.fn, self.signals = fn, signals

    def run(self):
        try:
            self.signals.ok.emit(self.fn())
        except api.ApiError as e:
            self.signals.err.emit(e.message)
        except Exception as e:  # noqa: BLE001  任何意外都不能让客户端崩
            traceback.print_exc()
            self.signals.err.emit(f"客户端出错了：{e}")


class Controller(QObject):
    def __init__(self, panel, overlay):
        super().__init__()
        self.panel, self.overlay = panel, overlay
        self.pool = QThreadPool.globalInstance()
        self._jobs: set[_Signals] = set()
        self.state = IDLE
        self._token = 0  # stop / 重新开始后，旧请求的结果直接丢掉
        self.detector = ChangeDetector()
        self.poll = QTimer(self)
        self.poll.setInterval(int(POLL_S * 1000))
        self.poll.timeout.connect(self._on_poll)

        panel.start_requested.connect(self.start)
        panel.stop_requested.connect(self.stop)
        panel.retry_requested.connect(self.retry)

    # ---- 对外接口 ----
    def start(self, app: str, goal: str, lang: str | None = None):
        self.stop()
        self.app, self.goal, self.lang = app, goal, lang or self.panel.lang()
        self.history: list[dict] = []
        self.expect = None
        self.guide = None
        self.last_instruction = None
        self.panel.set_running(True)
        self._request()

    def stop(self):
        self._token += 1
        self.poll.stop()
        self.overlay.clear()
        if self.state != IDLE:
            self.panel.set_status("已停止")
        self.state = IDLE
        self.panel.set_running(False)

    def retry(self):
        if self.state in (PAUSED, WAITING):
            self.poll.stop()
            self._request()

    # ---- 内部 ----
    def _bg(self, fn, on_ok, on_err):
        token = self._token
        sig = _Signals()
        self._jobs.add(sig)

        def done(handler):
            def f(v):
                self._jobs.discard(sig)
                if token == self._token:
                    handler(v)
            return f

        sig.ok.connect(done(on_ok))
        sig.err.connect(done(on_err))
        self.pool.start(_Job(fn, sig))

    def _request(self):
        self.state = REQUESTING
        self.poll.stop()
        self.panel.set_status("正在看你的屏幕…")
        with self.overlay.hidden():
            img = capture.grab()
        self._img = img
        payload = {
            "app": self.app,
            "goal": self.goal,
            "lang": self.lang,
            "history": self.history[-10:],
            "expect": self.expect,
            "guide": self.guide,
        }

        def call():
            return api.next_step({**payload, "screenshot": capture.to_jpeg_b64(img)})

        self._bg(call, self._on_result, self._on_error)

    def _on_result(self, r: dict):
        if self.history and r.get("previous_ok") is False:
            self.history[-1]["ok"] = False
        status, text = r.get("status"), r.get("instruction") or ""
        if status == "next" and r.get("box"):
            screen = QApplication.primaryScreen().geometry()
            rect = expand(box_to_rect(r["box"], screen.width(), screen.height()), HIGHLIGHT_PAD)
            self.overlay.show_target(rect, text)
            self.last_instruction = text
            self.expect = r.get("expect")
            self.panel.set_step(len(self.history) + 1, text)
            self.panel.set_status("做完这一步，框会自动跳到下一步")
            self._speak(text)
            self._wait()
        elif status == "done":
            self.overlay.clear()
            self.state = IDLE
            self.panel.set_step(len(self.history) + 1, text or "完成！")
            self.panel.set_status("完成！")
            self.panel.set_running(False)
            self._speak(text or "完成！")
        else:  # unsure
            self.overlay.clear()
            self.last_instruction = None
            self.panel.set_status(text or "看不出下一步该点哪里")
            self.panel.allow_retry(True)
            self._speak(text)
            self._wait()

    def _on_error(self, msg: str):
        self.poll.stop()
        self.state = PAUSED
        self.panel.set_error(msg)

    def _wait(self):
        self.state = WAITING
        self._need_baseline = True
        self.poll.start()

    def _frame(self):
        screen = QApplication.primaryScreen().geometry()
        g = self.panel.frameGeometry()
        masks = self.overlay.painted_rects() + [(g.x(), g.y(), g.width(), g.height())]
        small = capture.to_small_gray(capture.grab())
        return mask_rects(small, masks, screen.width(), screen.height())

    def _on_poll(self):
        if self.state != WAITING:
            return
        try:
            frame = self._frame()
        except Exception as e:  # noqa: BLE001
            self._on_error(f"截屏失败：{e}")
            return
        if self._need_baseline:  # 基准帧在高亮层显示之后取
            self.detector.reset(frame)
            self._need_baseline = False
            return
        if self.detector.feed(frame):
            if self.last_instruction:
                self.history.append({"instruction": self.last_instruction, "ok": True})
            self._request()

    def _speak(self, text: str):
        """C7 再接播放；现在先调接口，204 / 没有这个接口都静默跳过。"""
        if not text:
            return
        self._bg(lambda: api.speak(text, self.lang), lambda _mp3: None, lambda _msg: None)
