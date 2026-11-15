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

from showme import api, apps, capture, enroll, ocr, wording
from showme.change import ChangeDetector, mask_rects
from showme.config import HIGHLIGHT_PAD, LOG, MIC_MAX_S, POLL_S
from showme.coords import box_to_rect, expand, same_target
from showme.i18n import t
from showme.agent import AgentSession, MainThread
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
        self._guess: dict | None = None  # 上一次 next-step 返回的 next_guess（预测的下一步）
        self._provisional: dict | None = None  # OCR 秒出的框，等 Gemini 核对
        self._t_change: float | None = None  # 画面变化被检测到的时刻（算出框耗时）
        self.step_listener = self._agent_notify  # 新的一步出来时回调 (instruction, kind)
        self.main = MainThread()  # Agent 的 client tool 在 SDK 线程里跑，碰界面 / 截图要转到主线程
        self.agent: AgentSession | None = None
        self._enrolling = False
        self._stopped = False  # 按了 Stop：Agent 的 tool 调用一律返回 STOPPED，直到用户再开口 / 打字
        self._log_dir = None
        self._n_req = 0
        self._n_log = 0
        g = QApplication.primaryScreen().geometry()
        self._screen_wh = (g.width(), g.height())  # 后台线程里记日志要用，不能在那边调 Qt
        if LOG:
            self._log_dir = Path(__file__).resolve().parent.parent / "logs" / time.strftime("%Y%m%d-%H%M%S")
            self._log_dir.mkdir(parents=True, exist_ok=True)
            print(f"[log] 每一轮对话和每一步的截图 / 请求 / 返回存到 {self._log_dir}", flush=True)

        panel.message_submitted.connect(self.on_user_message)
        panel.mic_toggled.connect(self.toggle_mic)
        panel.live_toggled.connect(self.set_live)
        panel.filter_toggled.connect(self._set_filter)
        panel.tap_toggled.connect(self._set_tap_to_talk)
        panel.enroll_requested.connect(self.enroll_voice)
        panel.input_focus_changed.connect(self._on_input_focus)
        panel.stop_requested.connect(self.stop)
        panel.retry_requested.connect(self.retry)
        panel.add_message("assistant", t("welcome"))
        self._bg_free(ocr.warmup, lambda ms: print(f"[ocr] 热身完成 {ms:.0f} ms", flush=True), lambda e: None)

    # ================= 对话 =================
    def on_user_message(self, text: str):
        text = text.strip()
        if not text or self._chat_busy:
            return
        self.lang = self.panel.lang()
        self.panel.add_message("user", text)
        if self.agent and self.agent.active:  # 实时语音模式：打字也交给 Agent（同一个「大脑」）
            self._resume_after_stop()
            self._remember("user", text)
            if not self.agent.send(text):
                self.panel.set_error(t("live_unavailable"))
            return
        self._interrupted_request = self.state == REQUESTING
        if self.task is not None:  # 指导中途插话：先停检测，丢掉还没回来的 next-step
            self.poll.stop()
            self._token += 1
        self._chat_busy = True
        self.panel.set_busy(True)
        self.panel.set_status(t("thinking"))
        front = apps.frontmost_app()  # 截图前读
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
            "frontmost_app": front,
        }
        self._remember("user", text)
        print(f"[chat] → {text!r} frontmost_app={front!r} task={'有' if payload['task'] else '无'}", flush=True)
        t0 = time.time()

        def call():
            r = api.chat({**payload, "screenshot": capture.to_jpeg_b64(img)})
            self._log_turn("chat", img, payload, r, t0)
            return r

        def failed(e):
            self._log_turn("chat", img, payload, {"error": _msg(e)}, t0)
            self._on_chat_error(e)

        self._bg(call, self._on_chat, failed)

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
        if self.agent and self.agent.active:
            self._toggle_talk()
            return
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
    def _start_task(self, app: str, goal: str, outline: list[str] | None, request: bool = True):
        self._token += 1
        self.poll.stop()
        self.overlay.clear()
        self.task = {"app": app, "goal": goal}
        self.history, self.expect, self.current, self.last_instruction = [], None, None, None
        self.guide = outline or None
        self._n_req = 0
        self.panel.set_running(True)
        self._update_steps()
        if not self.guide:
            self._fetch_guide()
        if request:
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
        """面板上的「停止」按钮。实时语音模式下也要让 Agent 立刻安静、不再自己开始新任务。"""
        if self.agent and self.agent.active:
            self._stopped = True
            self.agent.hush()
            print("[agent] Stop：本地静音，Agent 已通知停下等待", flush=True)
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
        if self.agent:
            self.agent.stop()

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
    def _request(self, user_note: str | None = None, img=None, front: str | None = None):
        """img / front：画面变化时已经截好的图（OCR 秒出框用的同一张），不用再截一次。"""
        if self.task is None:
            return
        self.state = REQUESTING
        self.poll.stop()
        self.panel.set_status(t("looking"))
        if img is None:
            front = apps.frontmost_app()  # 截图前读
            try:
                with self.overlay.hidden():
                    img = capture.grab()
            except Exception as e:  # noqa: BLE001
                self._on_error(api.ApiError(t("capture_failed", e=e)))
                return
        self._req_img = img
        self._n_req += 1
        payload = {
            "app": self.task["app"],
            "goal": self.task["goal"],
            "lang": self.lang,
            "history": self.history[-10:],
            "expect": self.expect,
            "guide": self.guide,
            "frontmost_app": front,
        }
        if user_note:  # 只有 /api/chat 返回 advance 时才带
            payload["user_note"] = user_note
        t0 = time.time()

        def call():
            r = api.next_step({**payload, "screenshot": capture.to_jpeg_b64(img)})
            self._log_turn("next", img, payload, r, t0)
            return r

        def failed(e):
            self._log_turn("next", img, payload, {"error": _msg(e)}, t0)
            self._on_error(e)

        self._bg(call, self._on_result, failed)

    def _log_turn(self, kind: str, img, payload: dict, r: dict, t0: float):
        """SHOWME_LOG=1：存每一次请求的截图 + 请求（不含截图）+ 返回 + 耗时，方便联调复盘。后台线程里调用。"""
        if not self._log_dir:
            return
        try:
            self._n_log += 1
            k = f"{self._n_log:02d}_{kind}"
            img.convert("RGB").save(self._log_dir / f"{k}_screen.jpg", quality=80)
            rec = {"kind": kind, "seconds": round(time.time() - t0, 2), "request": payload, "response": r}
            if kind == "next" and r.get("box"):
                rec["rect_logical_xywh"] = expand(box_to_rect(r["box"], *self._screen_wh), HIGHLIGHT_PAD)
            (self._log_dir / f"{k}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
            brief = r.get("error") or (f"{r.get('action')} | {r.get('reply')}" if kind == "chat"
                                       else f"{r.get('status')} box={r.get('box')} | {r.get('instruction')}")
            print(f"[log] {k} {rec['seconds']}s {brief}", flush=True)
        except Exception as e:  # noqa: BLE001  记录失败不影响主流程
            print(f"[log] 记录失败：{e}")

    def _say_step(self, text: str):
        # 刚回复过用户：这一步排在回复后面读；否则打断上一句没读完的
        self.narrator.say(text, interrupt=not self._speak_after_reply)
        self._speak_after_reply = False

    def _show_step(self, box: list[int], text: str):
        rect = expand(box_to_rect(box, *self._screen_wh), HIGHLIGHT_PAD)
        self.overlay.show_target(rect, text)
        self.last_instruction = text

    def _speed_log(self, how: str):
        if self._t_change is not None:
            print(f"[speed] 画面变化 → 出框 {(time.perf_counter() - self._t_change) * 1000:.0f} ms（{how}）", flush=True)

    def _on_result(self, r: dict, source: str | None = None):
        self._result_source = source
        if self.history and r.get("previous_ok") is False:
            self.history[-1]["ok"] = False
        status, text = r.get("status"), r.get("instruction") or ""
        self.panel.set_status("")
        prov, self._provisional = self._provisional, None
        self._guess = r.get("next_guess") if status == "next" else None
        if status == "next" and r.get("box"):
            self.current = r
            self.expect = r.get("expect")
            if prov and same_target(prov["box"], r["box"]):
                # OCR 秒出的框是对的：框不动，只补上 Gemini 的 expect 等信息
                print(f"[speed] Gemini 核对：和 OCR 框一致（Gemini 用了 {(time.perf_counter() - self._t_change) * 1000:.0f} ms）", flush=True)
                self._wait(base_img=self._req_img)  # 用户可能在等 Gemini 时已经点了：用那张截图当基准帧
                return
            self._show_step(r["box"], text)
            self.panel.allow_retry(True)
            self._update_steps()
            if prov:
                print(f"[speed] Gemini 更正：OCR 框 {prov['box']} → Gemini 框 {r['box']}", flush=True)
                self.panel.add_message("step", t("step_fixed", n=len(self.history) + 1, text=text))
                self.narrator.say(text, interrupt=True)
                self._notify(text, "correction")
                self._wait(base_img=self._req_img)
            else:
                self._speed_log("等 Gemini")
                self.panel.add_step(len(self.history) + 1, text)
                self._say_step(text)
                self._notify(text, "gemini")
                self._wait()
        elif status == "done":
            self.overlay.clear()
            outro = wording.outro(self.task["app"], self.task["goal"], len(self.history), self.lang, text)
            self.panel.add_message("assistant", outro)
            self._remember("assistant", outro)
            self.panel.set_steps([h["instruction"] for h in self.history], None, [])
            self.narrator.say(outro, interrupt=not self._speak_after_reply)
            self._speak_after_reply = False
            self._notify(text or t("done"), "done")
            self._end_task(t("done"))
        else:  # unsure：比如要点的东西被挡住了 / 窗口没打开。画面一变就自动再问，也可以按「重试」
            self.overlay.clear()
            self._notify(text or t("unsure"), "unsure")
            self.current, self.last_instruction = None, None
            msg = text or t("unsure")
            self.panel.add_message("assistant", msg)
            self._remember("assistant", msg)
            self.panel.set_status(t("unsure_hint"))
            self.panel.allow_retry(True)
            self._say_step(msg)
            self._wait()

    def _on_error(self, e):
        self.poll.stop()
        self.state = PAUSED
        self.panel.set_status("")
        self.panel.set_error(_msg(e))

    # ================= 画面变化检测 =================
    def _wait(self, base_img=None):
        """base_img：用这张截图当基准帧（OCR 秒出框后，用户可能在等 Gemini 时就已经点了）。"""
        self.state = WAITING
        self._base_raw = capture.to_small_gray(base_img) if base_img is not None else None
        self._masks: list[tuple[int, int, int, int]] = []
        self._t_change = None
        self.poll.start()

    def _notify(self, text: str, kind: str):
        if getattr(self, "_result_source", None) == "tool" and kind != "ocr":
            return  # Agent 自己通过 show_next_step 要的这一步：它会念 tool 的返回值
        if self.step_listener:
            try:
                self.step_listener(text, kind)
            except Exception as e:  # noqa: BLE001
                print(f"[agent] step_listener 出错：{e}", flush=True)

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
            self._on_screen_changed()

    def _on_screen_changed(self):
        self._result_source = None
        self._t_change = time.perf_counter()
        if self.last_instruction:
            self.history.append({"instruction": self.last_instruction, "ok": True})
        guess = self._guess if isinstance(self._guess, dict) else ({"label": self._guess} if isinstance(self._guess, str) else None)
        self._guess = None
        label = (guess or {}).get("label")
        front = apps.frontmost_app()
        try:
            with self.overlay.hidden():
                img = capture.grab()
        except Exception as e:  # noqa: BLE001
            self._on_error(api.ApiError(t("capture_failed", e=e)))
            return
        self._request(img=img, front=front)  # Gemini 照常核对
        if not label:
            return
        instr = guess.get("instruction") or t("click_label", label=label)
        token = self._token

        def found(res):
            box, ms = res
            # Gemini 已经先回来了 / 任务变了：OCR 结果作废
            if token != self._token or self.state != REQUESTING or box is None:
                if box is None:
                    print(f"[speed] OCR 没找到 {label!r}（{ms:.0f} ms），等 Gemini", flush=True)
                return
            self._provisional = {"box": box, "instruction": instr}
            self._show_step(box, instr)
            self._speed_log(f"OCR 秒出，识别 {ms:.0f} ms")
            self.panel.add_step(len(self.history) + 1, instr)
            self._update_steps()
            self._say_step(instr)
            self._notify(instr, "ocr")

        self._bg_free(lambda: ocr.locate(img, label), found, lambda e: print(f"[ocr] {_msg(e)}", flush=True))

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

    # ================= 实时语音（ElevenLabs Agent）=================
    def set_live(self, on: bool):
        """面板「Live voice」开关。连不上会自动退回打字模式。"""
        if on and not (self.agent and self.agent.active):
            self.lang = self.panel.lang()
            self.agent = AgentSession({"show_next_step": self.tool_show_next_step,
                                       "look_at_screen": self.tool_look_at_screen}, self.lang)
            self.agent.connected.connect(self._on_agent_connected)
            self.agent.failed.connect(self._on_agent_failed)
            self.agent.user_said.connect(self._on_agent_user)
            self.agent.agent_said.connect(self._on_agent_reply)
            self.agent.voice_event.connect(self._on_voice_event)
            self.panel.set_status(t("live_connecting"))
            self.agent.start()
        elif not on and self.agent:
            self.agent.stop()
            self.agent = None
            self.narrator.muted = False
            self.panel.set_live_state(False)
            self.panel.set_status(t("live_off"))

    def _on_agent_connected(self, engine_name: str):
        self.narrator.stop()
        self.narrator.muted = True
        self.panel.set_live_state(True)
        g = self.agent.gate
        self.panel.set_status(t("live_on", engine=engine_name))
        if g and not g.voiceprints:
            self.panel.add_message("assistant", t("no_voiceprint"))
        if g:
            g.filter_enabled = self.panel.filter_box.isChecked()
            g.push_to_talk = self.panel.tap_box.isChecked()

    def _on_agent_failed(self, why: str):
        self.agent = None
        self.narrator.muted = False
        self.panel.set_live_state(False)
        self.panel.set_error(t("live_unavailable"))
        print(f"[agent] 退回打字模式：{why}", flush=True)

    def _resume_after_stop(self):
        if self._stopped:
            self._stopped = False
            if self.agent:
                self.agent.unhush()
            print("[agent] 用户又开口了：取消 Stop 状态", flush=True)

    def _on_agent_user(self, text: str):
        self._resume_after_stop()
        self.panel.add_message("user", text)
        self._remember("user", text)

    def _on_agent_reply(self, text: str):
        if text.strip():
            self.panel.add_message("assistant", text)
            self._remember("assistant", text)

    def _on_voice_event(self, kind: str, score):
        if kind == "duck":
            self.panel.set_status(t("hearing"))
        elif kind == "reject":
            self.panel.set_status(t("ignored_voice"))
        elif kind == "accept":
            self._resume_after_stop()
            self.panel.set_status(t("listening_agent"))

    def _agent_notify(self, text: str, kind: str):
        """画面变化后的新一步 → 让 Agent 念出来。"""
        if not (self.agent and self.agent.active):
            return
        prefix = {"ocr": "Next", "gemini": "Next", "correction": "Correction", "done": "DONE", "unsure": "UNSURE"}.get(kind)
        if prefix:
            self.agent.send(f"[screen changed] {prefix}: {text}")

    # ---- client tools（在 SDK 线程里调用）----
    def tool_show_next_step(self, params: dict) -> str:
        if self._stopped:
            return "STOPPED: the user pressed Stop"
        goal = (params.get("goal") or "").strip()
        note = (params.get("note") or "").strip() or None
        t0 = time.time()
        try:
            img, payload = self.main.call(lambda: self._tool_prepare(goal, note))
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"
        try:
            r = api.next_step({**payload, "screenshot": capture.to_jpeg_b64(img)})
        except api.ApiError as e:
            self._log_turn("next", img, payload, {"error": e.message}, t0)
            self.main.call(lambda: self._on_error(e))
            return f"ERROR: {e.message}"
        self._log_turn("next", img, payload, r, t0)
        self.main.call(lambda: self._on_result(r, source="tool"))
        status, text = r.get("status"), r.get("instruction") or ""
        return text if status == "next" else f"{'DONE' if status == 'done' else 'UNSURE'}: {text}"

    def _tool_prepare(self, goal: str, note: str | None):
        """主线程：决定是不是新任务，截图，组 next-step 请求。"""
        self._token += 1  # 丢掉还没回来的 next-step / OCR
        self.poll.stop()
        self._provisional = None
        if self.task is None or (not note and goal and _different_goal(goal, self.task["goal"])):
            self._start_task(apps.frontmost_app() or t("current_app"), goal or t("current_app"), None, request=False)
        elif goal:
            self.task["goal"] = goal
        if note and self.last_instruction:  # 「做完了 / 点错了」：当前这步记进 history，带上 note 重新看
            self.history.append({"instruction": self.last_instruction, "ok": None})
        self.state = REQUESTING
        self.panel.set_status(t("looking"))
        front = apps.frontmost_app()
        with self.overlay.hidden():
            img = capture.grab()
        self._req_img = img
        self._n_req += 1
        payload = {"app": self.task["app"], "goal": self.task["goal"], "lang": self.lang, "history": self.history[-10:],
                   "expect": self.expect, "guide": self.guide, "frontmost_app": front}
        if note:
            payload["user_note"] = note
        return img, payload

    def tool_look_at_screen(self, params: dict) -> str:
        if self._stopped:
            return "STOPPED: the user pressed Stop"
        q = (params.get("question") or "").strip() or "What is on the screen?"
        t0 = time.time()
        try:
            img, payload = self.main.call(lambda: self._tool_chat_prepare(q))
            r = api.chat({**payload, "screenshot": capture.to_jpeg_b64(img)})
        except api.ApiError as e:
            return f"ERROR: {e.message}"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"
        self._log_turn("chat", img, payload, r, t0)
        return r.get("reply") or "I'm not sure."

    def _tool_chat_prepare(self, q: str):
        front = apps.frontmost_app()
        with self.overlay.hidden():
            img = capture.grab()
        return img, {"lang": self.lang, "message": q, "messages": self.messages[-MAX_MESSAGES:],
                     "task": self._task_state(), "frontmost_app": front}

    # ---- 面板开关 ----
    def _set_filter(self, on: bool):
        if self.agent and self.agent.gate:
            self.agent.gate.filter_enabled = on
        print(f"[voice] 声纹过滤 {'开' if on else '关'}", flush=True)

    def _set_tap_to_talk(self, on: bool):
        """「点一下开始收音、再点一下结束」模式（现场太吵时用）。"""
        if self.agent and self.agent.gate:
            self.agent.gate.push_to_talk = on
            self.agent.gate.key_down = False
        self.panel.set_talking(False)
        print(f"[voice] 点按收音 {'开' if on else '关'}", flush=True)

    def _toggle_talk(self):
        g = self.agent.gate if self.agent else None
        if not g:
            return
        if not g.push_to_talk:  # 没开点按模式时，麦克风按钮直接切到点按模式并开始收音
            g.push_to_talk = True
            self.panel.tap_box.setChecked(True)
        g.key_down = not g.key_down
        if g.key_down:
            self._resume_after_stop()
        self.panel.set_talking(g.key_down)
        print(f"[voice] 收音 {'开始' if g.key_down else '结束'}", flush=True)

    # ---- 录入声纹（面板按钮）----
    def enroll_voice(self, name: str):
        if self._enrolling or not name:
            return
        self._enrolling = True
        was_live = bool(self.agent and self.agent.active)
        if was_live:  # 录音和实时对话不能同时占用麦克风
            self.set_live(False)
        self.panel.set_enrolling(True)

        def prompt(i, n, s):
            self.main.call(lambda: self.panel.add_message("step", t("enroll_prompt", i=i, n=n, s=s)))
            import subprocess
            subprocess.run(["afplay", "/System/Library/Sounds/Tink.aiff"])

        def retry(i, lvl):
            self.main.call(lambda: self.panel.add_message("error", t("enroll_retry", i=i)))

        def work():
            from showme.audio_engine import record_clip
            from showme.voice_filter import SpeakerModel

            ambient = enroll.level_db(record_clip(1.5))  # 先听背景：吵的地方不开口也有 -27 dBFS
            min_level = max(enroll.MIN_LEVEL_DB, ambient + enroll.ABOVE_AMBIENT_DB)
            print(f"[voice] 录声纹：背景 {ambient:.1f} dBFS，要求至少 {min_level:.1f}", flush=True)
            vp, levels = enroll.enroll(record_clip, SpeakerModel().embed, prompt, on_retry=retry, min_level_db=min_level)
            return enroll.save_named(name, vp), levels

        def ok(res):
            path, levels = res
            self.panel.add_message("assistant", t("enroll_done", name=name))
            print(f"[voice] 已保存 {name} 的声纹 {path}，音量 {levels}", flush=True)
            finish()

        def fail(e):
            self.panel.set_error(t("enroll_failed", e=_msg(e)))
            finish()

        def finish():
            self._enrolling = False
            self.panel.set_enrolling(False)
            if was_live:
                self.panel.live_box.setChecked(True)  # 重新连接，会加载新声纹

        self._bg_free(work, ok, fail)


def _different_goal(a: str, b: str) -> bool:
    """两个目标是不是明显不同（词重合很少）。Agent 每次调用时措辞会变，不能一变就当成新任务。"""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return len(wa & wb) / max(1, len(wa | wb)) < 0.3
