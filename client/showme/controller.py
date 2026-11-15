"""状态机（对话模式）。网络请求都在后台线程，界面不卡。

用户发一句话（打字 / 语音）→ 截图 → /api/chat → 按 action：
  start   → 设置 app / goal / outline，清空 history → next-step 流程
  resume  → 只回答问题：保留框，重新取基准帧继续检测画面变化
  advance → 用户说做完 / 跳过 / 点错 → 带 user_note 再调 next-step
  stop    → 清框，回到 IDLE
  clarify → 只显示回复，等下一句

next-step 流程：
REQUESTING ─ next   → 画框，对话里加「第 N 步」→ WAITING
           ─ done   → 清框，结束语 → IDLE
           ─ unsure → 清框，显示提示 → WAITING（画面一变就自动再问；也可以按「重试」）
           ─ 出错   → 对话里显示错误 → PAUSED（按「重试」再问）
WAITING: 每 POLL_S 秒截图（不隐藏高亮层，遮掉框和面板）→ 画面变了且稳定 → 记进 history → REQUESTING
用户在输入框打字 / 录音时暂停画面检测：点面板会让原来的软件失去焦点，界面变化不能算「做完了」。
"""

import json
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from showme import api, apps, capture, wording
from showme.change import ChangeDetector, mask_rects
from showme.config import HIGHLIGHT_PAD, LOG, MIC_MAX_S, POLL_S
from showme.coords import box_to_rect, expand
from showme.i18n import t
from showme.narrator import Narrator
from showme.recorder import Recorder

IDLE, REQUESTING, WAITING, PAUSED = "IDLE", "REQUESTING", "WAITING", "PAUSED"
MAX_MESSAGES = 10


class _Signals(QObject):
    ok = pyqtSignal(object)
    err = pyqtSignal(object)  # ApiError 或 str


class _Job(QRunnable):
    def __init__(self, fn, signals: _Signals):
        super().__init__()
        self.fn, self.signals = fn, signals

    def run(self):
        try:
            self.signals.ok.emit(self.fn())
        except api.ApiError as e:
            self.signals.err.emit(e)
        except Exception as e:  # noqa: BLE001  任何意外都不能让客户端崩
            traceback.print_exc()
            self.signals.err.emit(api.ApiError(t("client_error", e=e)))


def _msg(e) -> str:
    return e.message if isinstance(e, api.ApiError) else str(e)


class Controller(QObject):
    def __init__(self, panel, overlay):
        super().__init__()
        self.panel, self.overlay = panel, overlay
        self.pool = QThreadPool.globalInstance()
        self._jobs: set[_Signals] = set()
        self.state = IDLE
        self._token = 0  # stop / 用户插话后，还没回来的 next-step 结果直接丢掉
        self.detector = ChangeDetector()
        self.poll = QTimer(self)
        self.poll.setInterval(int(POLL_S * 1000))
        self.poll.timeout.connect(self._on_poll)
        self.lang = panel.lang()
        self.narrator = Narrator(overlay, self._bg_free, panel.voice_enabled, lambda: self.lang)
        self.recorder = Recorder()
        self.mic_timer = QTimer(self)
        self.mic_timer.setSingleShot(True)
        self.mic_timer.timeout.connect(self._stop_recording)
        self.app_timer = QTimer(self)  # 每秒记一次最前面的 App，用户直接点面板也能拿到之前用的软件
        self.app_timer.timeout.connect(apps.remember)
        self.app_timer.start(1000)

        self.messages: list[dict] = []  # 对话记录 {role, text}
        self.task: dict | None = None  # {app, goal}，没有任务时为 None
        self.current: dict | None = None  # 当前这一步（上一次 next-step 的返回）
        self.history: list[dict] = []
        self.guide = None
        self.expect = None
        self.last_instruction = None
        self._chat_busy = False
        self._typing = False
        self._speak_after_reply = False
        self._interrupted_request = False
        self._log_dir = None
        self._n_req = 0

        panel.message_submitted.connect(self.on_user_message)
        panel.mic_toggled.connect(self.toggle_mic)
        panel.input_focus_changed.connect(self._on_input_focus)
        panel.stop_requested.connect(self.stop)
        panel.retry_requested.connect(self.retry)
        panel.add_message("assistant", t("welcome"))

    # ================= 对话 =================
    def on_user_message(self, text: str):
        text = text.strip()
        if not text or self._chat_busy:
            return
        self.lang = self.panel.lang()
        self.panel.add_message("user", text)
        self._interrupted_request = self.state == REQUESTING
        if self.task is not None:  # 指导中途插话：先停检测，丢掉还没回来的 next-step
            self.poll.stop()
            self._token += 1
        self._chat_busy = True
        self.panel.set_busy(True)
        self.panel.set_status(t("thinking"))
        try:
            with self.overlay.hidden():
                img = capture.grab()
        except Exception as e:  # noqa: BLE001
            self._on_chat_error(api.ApiError(t("capture_failed", e=e)))
            return
        payload = {
            "lang": self.lang,
            "message": text,
            "messages": self.messages[-MAX_MESSAGES:],  # 不含本次这句
            "task": self._task_state(),
            "frontmost_app": apps.frontmost_app(),
        }
        self._remember("user", text)
        print(f"[chat] → {text!r} frontmost_app={payload['frontmost_app']!r} task={'有' if payload['task'] else '无'}", flush=True)
        self._bg(lambda: api.chat({**payload, "screenshot": capture.to_jpeg_b64(img)}),
                 self._on_chat, self._on_chat_error)

    def _task_state(self) -> dict | None:
        if self.task is None:
            return None
        cur = None
        if self.current:
            cur = {k: self.current.get(k) for k in ("instruction", "target_label", "box", "expect")}
        return {**self.task, "outline": self.guide, "history": self.history[-10:], "current": cur}

    def _remember(self, role: str, text: str):
        self.messages.append({"role": role, "text": text})
        del self.messages[:-MAX_MESSAGES]

    def _on_chat(self, r: dict):
        self._chat_busy = False
        self.panel.set_busy(False)
        self.panel.set_status("")
        reply, action = r.get("reply") or "", r.get("action")
        print(f"[chat] ← action={action} reply={reply!r}", flush=True)
        if reply:
            self.panel.add_message("assistant", reply)
            self._remember("assistant", reply)
            self.narrator.say(reply, interrupt=True)
            self._speak_after_reply = True  # 接下来那一步排在回复后面读，不打断它
        if action == "start" and r.get("goal"):
            self._start_task(r.get("app") or apps.frontmost_app() or t("current_app"), r["goal"], r.get("outline"))
        elif action == "advance" and self.task is not None:
            if self.last_instruction:
                self.history.append({"instruction": self.last_instruction, "ok": None})
            self._request(user_note=r.get("note"))
        elif action == "stop" and self.task is not None:
            self._end_task(t("stopped"))
        elif self.task is not None:  # resume / clarify
            self._resume()

    def _on_chat_error(self, e):
        self._chat_busy = False
        self.panel.set_busy(False)
        self.panel.set_status("")
        self.panel.set_error(_msg(e))
        if self.task is not None:
            self._resume()

    def _resume(self):
        """回到当前这一步：框还在就重新等画面变化；没框（刚才的请求被丢掉 / 出错）就重新问。"""
        if self.current and self.overlay.painted_rects() and not self._interrupted_request:
            self._wait()
        else:
            self._request()

    # ================= 语音输入 =================
    def toggle_mic(self):
        if self.recorder.recording:
            self._stop_recording()
            return
        self.narrator.stop()  # 别把 ShowMe 自己的声音录进去
        self.poll.stop()
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001  没麦克风 / 没权限
            print(f"[mic] 开始录音失败：{e}", flush=True)
            self.panel.set_error(t("mic_failed", e=e))
            if self.state == WAITING:
                self._wait()
            return
        self.panel.set_recording(True)
        self.mic_timer.start(MIC_MAX_S * 1000)

    def _stop_recording(self):
        self.mic_timer.stop()
        if not self.recorder.recording:
            return
        wav = self.recorder.stop()
        self.panel.set_recording(False)
        self.panel.set_status(t("transcribing"))
        self.panel.set_busy(True)
        lang = self.panel.lang()
        self._bg_free(lambda: api.transcribe(wav, lang), self._on_transcribed, self._on_transcribe_error)

    def _on_transcribed(self, text: str):
        self.panel.set_busy(False)
        self.panel.set_status("")
        if not text:
            self.panel.add_message("assistant", t("not_heard"))
            if self.state == WAITING:
                self._wait()
            return
        self.on_user_message(text)

    def _on_transcribe_error(self, e):
        self.panel.set_busy(False)
        self.panel.set_status("")
        self.panel.set_error(_msg(e))
        if self.state == WAITING:
            self._wait()

    def _on_input_focus(self, focused: bool):
        if focused and self.state == WAITING:
            self.poll.stop()
            self._typing = True
        elif not focused and self._typing:
            self._typing = False
            if self.state == WAITING and not self._chat_busy:
                self._wait()

    # ================= 任务 =================
    def _start_task(self, app: str, goal: str, outline: list[str] | None):
        self._token += 1
        self.poll.stop()
        self.overlay.clear()
        self.task = {"app": app, "goal": goal}
        self.history, self.expect, self.current, self.last_instruction = [], None, None, None
        self.guide = outline or None
        self._n_req = 0
        self._log_dir = None
        if LOG:
            self._log_dir = Path(__file__).resolve().parent.parent / "logs" / time.strftime("%Y%m%d-%H%M%S")
            self._log_dir.mkdir(parents=True, exist_ok=True)
            print(f"[log] 每一步的截图和后端返回存到 {self._log_dir}", flush=True)
        self.panel.set_running(True)
        self._update_steps()
        if not self.guide:
            self._fetch_guide()
        self._request()

    def _end_task(self, status: str = ""):
        self._token += 1
        self.poll.stop()
        self.overlay.clear()
        self.task, self.current, self.last_instruction = None, None, None
        self.state = IDLE
        self.panel.set_running(False)
        self.panel.set_status(status)

    def stop(self):
        """面板上的「停止」按钮。"""
        self.narrator.stop()
        if self.recorder.recording:
            self.recorder.stop()
            self.panel.set_recording(False)
        self._chat_busy = False
        self.panel.set_busy(False)
        self._end_task(t("stopped"))

    def shutdown(self):
        """退出前调用：停掉轮询、语音、录音，后面回来的请求结果都丢掉。"""
        self._token += 1
        self.poll.stop()
        self.app_timer.stop()
        self.narrator.stop()
        if self.recorder.recording:
            self.recorder.stop()

    def retry(self):
        if self.task is not None and self.state in (PAUSED, WAITING) and not self._chat_busy:
            self.poll.stop()
            self._request()

    # ================= 后台任务 =================
    def _bg(self, fn, on_ok, on_err):
        """结果回来时如果任务已经变了（_token 变了）就丢掉。"""
        self._run(fn, on_ok, on_err, self._token)

    def _bg_free(self, fn, on_ok, on_err):
        """不受 _token 影响（语音、转文字：它们自己管自己）。"""
        self._run(fn, on_ok, on_err, None)

    def _run(self, fn, on_ok, on_err, token):
        sig = _Signals()
        self._jobs.add(sig)

        def done(handler):
            def f(v):
                self._jobs.discard(sig)
                if token is None or token == self._token:
                    handler(v)
            return f

        sig.ok.connect(done(on_ok))
        sig.err.connect(done(on_err))
        self.pool.start(_Job(fn, sig))

    # ================= next-step =================
    def _request(self, user_note: str | None = None):
        if self.task is None:
            return
        self.state = REQUESTING
        self.poll.stop()
        self.panel.set_status(t("looking"))
        try:
            with self.overlay.hidden():
                img = capture.grab()
        except Exception as e:  # noqa: BLE001
            self._on_error(api.ApiError(t("capture_failed", e=e)))
            return
        self._img = img
        self._n_req += 1
        self._t_req = time.time()
        payload = {
            "app": self.task["app"],
            "goal": self.task["goal"],
            "lang": self.lang,
            "history": self.history[-10:],
            "expect": self.expect,
            "guide": self.guide,
            "user_note": user_note,
            "frontmost_app": apps.frontmost_app(),
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

    def _say_step(self, text: str):
        # 刚回复过用户：这一步排在回复后面读；否则打断上一句没读完的
        self.narrator.say(text, interrupt=not self._speak_after_reply)
        self._speak_after_reply = False

    def _on_result(self, r: dict):
        if self.history and r.get("previous_ok") is False:
            self.history[-1]["ok"] = False
        status, text = r.get("status"), r.get("instruction") or ""
        self.panel.set_status("")
        if status == "next" and r.get("box"):
            screen = QApplication.primaryScreen().geometry()
            rect = expand(box_to_rect(r["box"], screen.width(), screen.height()), HIGHLIGHT_PAD)
            self._log(r, rect)
            self.overlay.show_target(rect, text)
            self.current = r
            self.last_instruction = text
            self.expect = r.get("expect")
            self.panel.add_step(len(self.history) + 1, text)
            self.panel.allow_retry(True)
            self._update_steps()
            self._say_step(text)
            self._wait()
        elif status == "done":
            self._log(r, None)
            outro = wording.outro(self.task["app"], self.task["goal"], len(self.history), self.lang, text)
            self.panel.add_message("assistant", outro)
            self._remember("assistant", outro)
            self.panel.set_steps([h["instruction"] for h in self.history], None, [])
            self.narrator.say(outro, interrupt=not self._speak_after_reply)
            self._speak_after_reply = False
            self._end_task(t("done"))
        else:  # unsure：比如要点的东西被挡住了 / 窗口没打开。画面一变就自动再问，也可以按「重试」
            self._log(r, None)
            self.overlay.clear()
            self.current, self.last_instruction = None, None
            msg = text or t("unsure")
            self.panel.add_message("assistant", msg)
            self._remember("assistant", msg)
            self.panel.set_status(t("unsure_hint"))
            self.panel.allow_retry(True)
            self._say_step(msg)
            self._wait()

    def _on_error(self, e):
        if self._log_dir:
            self._log({"error": _msg(e)}, None)
        self.poll.stop()
        self.state = PAUSED
        self.panel.set_status("")
        self.panel.set_error(_msg(e))

    # ================= 画面变化检测 =================
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
        字幕消失 / 换一句、面板里多一条消息，都不能被当成「画面变了」。返回遮罩有没有变大。"""
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
            self._on_error(api.ApiError(t("capture_failed", e=e)))
            return
        grew = self._grow_masks()
        if self._base_raw is None:  # 基准帧在高亮层显示之后取
            self._base_raw = raw
            self.detector.reset(self._masked(raw))
            return
        if grew:  # 遮罩变大了：基准帧用同样的遮罩重算
            self.detector.reset(self._masked(self._base_raw))
        if self.detector.feed(self._masked(raw)):
            if self.last_instruction:
                self.history.append({"instruction": self.last_instruction, "ok": True})
            self._request()

    def _fetch_guide(self):
        """后端用官方文档生成步骤大纲。没有这个接口 / 失败都没关系。"""
        app, goal, lang = self.task["app"], self.task["goal"], self.lang

        def got(g):
            if g and g.get("outline") and not self.guide:
                self.guide = g["outline"]
                self._update_steps()

        self._bg(lambda: api.guide(app, goal, lang), got, lambda _e: None)

    def _update_steps(self):
        done = [h["instruction"] for h in self.history]
        n = len(done) + (1 if self.last_instruction else 0)
        upcoming = (self.guide or [])[n:]
        self.panel.set_steps(done, self.last_instruction, upcoming)
