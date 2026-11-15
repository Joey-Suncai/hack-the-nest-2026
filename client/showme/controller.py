"""状态机：截图 → 问后端 → 画框 → 等画面变化 → 下一步。网络请求都在后台线程，界面不卡。

IDLE --start--> REQUESTING
REQUESTING ─ next   → 画框 → WAITING
           ─ done   → 清框，「完成！」→ IDLE
           ─ unsure → 清框，显示提示 → WAITING（画面变了或按「重试」再问）
           ─ 出错   → 面板显示错误 → PAUSED（按「重试」再问）
WAITING: 每 POLL_S 秒截图（不隐藏高亮层，遮掉框和面板）→ 画面变了且稳定 → 记进 history → REQUESTING
stop：任何状态 → 清框 → IDLE
"""

import json
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from showme import api, capture, wording
from showme.i18n import t
from showme.change import ChangeDetector, mask_rects
from showme.config import HIGHLIGHT_PAD, LOG, POLL_S
from showme.coords import box_to_rect, expand
from showme.narrator import Narrator

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
            self.signals.err.emit(t("client_error", e=e))


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
        self.lang = "zh"
        self.narrator = Narrator(overlay, self._bg, panel.voice_enabled, lambda: self.lang)

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
        self._log_dir = None
        self._n_req = 0
        if LOG:
            self._log_dir = Path(__file__).resolve().parent.parent / "logs" / time.strftime("%Y%m%d-%H%M%S")
            self._log_dir.mkdir(parents=True, exist_ok=True)
            print(f"[log] 每一步的截图和后端返回存到 {self._log_dir}", flush=True)
        self.panel.set_running(True)
        self.narrator.say(wording.intro(app, goal, self.lang))
        self._fetch_guide()
        self._request()

    def stop(self):
        self._token += 1
        self.poll.stop()
        self.overlay.clear()
        self.narrator.stop()
        if self.state != IDLE:
            self.panel.set_status(t("stopped"))
        self.state = IDLE
        self.panel.set_running(False)

    def shutdown(self):
        """退出前调用：停掉轮询、语音，后面回来的请求结果都丢掉。"""
        self._token += 1
        self.poll.stop()
        self.narrator.stop()

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
        self.panel.set_status(t("looking"))
        with self.overlay.hidden():
            img = capture.grab()
        self._img = img
        self._n_req += 1
        self._t_req = time.time()
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

    def _log(self, r: dict, rect):
        """SHOWME_LOG=1：存发给后端的截图 + 返回 + 换算后的框，方便联调复盘。"""
        if not self._log_dir:
            return
        try:
            k = f"{self._n_req:02d}"
            self._img.convert("RGB").save(self._log_dir / f"{k}_screen.jpg", quality=80)
            rec = {"request": self._n_req, "seconds": round(time.time() - self._t_req, 2),
                   "history": self.history[-10:], "response": r, "rect_logical_xywh": rect}
            (self._log_dir / f"{k}_result.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
            print(f"[log] #{k} {rec['seconds']}s {r.get('status')} box={r.get('box')} rect={rect} | {r.get('instruction')}", flush=True)
        except Exception as e:  # noqa: BLE001  记录失败不影响主流程
            print(f"[log] 记录失败：{e}")

    def _on_result(self, r: dict):
        if self.history and r.get("previous_ok") is False:
            self.history[-1]["ok"] = False
        status, text = r.get("status"), r.get("instruction") or ""
        if status == "next" and r.get("box"):
            screen = QApplication.primaryScreen().geometry()
            rect = expand(box_to_rect(r["box"], screen.width(), screen.height()), HIGHLIGHT_PAD)
            self._log(r, rect)
            self.overlay.show_target(rect, text)
            self.last_instruction = text
            self.expect = r.get("expect")
            self.panel.set_step(len(self.history) + 1, text)
            self.panel.set_status(t("do_step"))
            self._update_steps()
            # 第 1 步排在开场白后面；之后每一步打断上一句没读完的
            self.narrator.say(text, interrupt=bool(self.history))
            self._wait()
        elif status == "done":
            self._log(r, None)
            self.overlay.clear()
            self.state = IDLE
            self.panel.step_label.setText(t("done"))
            self.panel.set_status("")
            self.panel.set_steps([h["instruction"] for h in self.history], None, [])
            self.panel.set_running(False)
            self.narrator.say(wording.outro(self.app, self.goal, len(self.history), self.lang, text), interrupt=True)
        else:  # unsure
            self._log(r, None)
            self.overlay.clear()
            self.last_instruction = None
            # 比如要点的东西被挡住了 / 窗口没打开：提示用户先做什么，画面一变就自动再问（也可以按「重试」）
            self.panel.set_status(f"{text or t('unsure')}\n{t('unsure_hint')}")
            self.panel.allow_retry(True)
            self.narrator.say(text, interrupt=True)
            self._wait()

    def _on_error(self, msg: str):
        if getattr(self, "_log_dir", None):
            self._log({"error": msg}, None)
        self.poll.stop()
        self.state = PAUSED
        self.panel.set_error(msg)

    def _wait(self):
        self.state = WAITING
        self._base_raw = None
        self._masks: list[tuple[int, int, int, int]] = []
        self.poll.start()

    def _masked(self, small):
        screen = QApplication.primaryScreen().geometry()
        return mask_rects(small, self._masks, screen.width(), screen.height())

    def _grow_masks(self) -> bool:
        """把高亮框、气泡、字幕、面板占过的区域都遮住。同一步里只增不减：
        字幕消失 / 换一句，不能被当成「画面变了」。返回遮罩有没有变大。"""
        g = self.panel.frameGeometry()
        grew = False
        for r in self.overlay.painted_rects() + [(g.x(), g.y(), g.width(), g.height())]:
            if r not in self._masks:
                self._masks.append(r)
                grew = True
        return grew

    def _on_poll(self):
        if self.state != WAITING:
            return
        try:
            raw = capture.to_small_gray(capture.grab())
        except Exception as e:  # noqa: BLE001
            self._on_error(t("capture_failed", e=e))
            return
        grew = self._grow_masks()
        if self._base_raw is None:  # 基准帧在高亮层显示之后取
            self._base_raw = raw
            self.detector.reset(self._masked(raw))
            return
        if grew:  # 新出现了一句字幕：基准帧用同样的遮罩重算
            self.detector.reset(self._masked(self._base_raw))
        if self.detector.feed(self._masked(raw)):
            if self.last_instruction:
                self.history.append({"instruction": self.last_instruction, "ok": True})
            self._request()

    def _fetch_guide(self):
        """加分项：后端用官方文档生成步骤大纲。没有这个接口 / 失败都没关系。"""
        app, goal, lang = self.app, self.goal, self.lang

        def got(g):
            if g and g.get("outline"):
                self.guide = g["outline"]
                self._update_steps()

        self._bg(lambda: api.guide(app, goal, lang), got, lambda _m: None)

    def _update_steps(self):
        done = [h["instruction"] for h in self.history]
        n = len(done) + (1 if self.last_instruction else 0)
        upcoming = (self.guide or [])[n:]
        self.panel.set_steps(done, self.last_instruction, upcoming)
