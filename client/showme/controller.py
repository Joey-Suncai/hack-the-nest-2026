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
import re
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from showme import api, apps, ax_snap, capture, enroll, ocr, sync, wording
from showme.change import ChangeDetector, _diff, mask_rects
from showme.config import (CHANGE_THRESHOLD, CLICK_EXTRA_PAD, DEBUG_DETECT, HIGHLIGHT_PAD, LOG, MIC_MAX_S, POLL_S,
                           NEAR_CLICK_PT, SETTLE_MAX_S, SETTLE_MIN_S, SETTLE_POLL_S, TALK_MAX_S)
from showme.coords import box_to_rect, expand, same_target
from showme.i18n import t
from showme.agent import AgentSession, MainThread
from showme.hotkey import RightOptionMonitor
from showme.inputwatch import InputWatcher, click_in_rect, near_rect, step_kind
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


def dbg(*a):
    if DEBUG_DETECT:
        print(f"[detect {time.strftime('%H:%M:%S')}]", *a, flush=True)


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
        self._sync_step: int | None = None  # 计时：当前显示的是第几步（[sync] 日志）
        self._user_turn_since_step = True  # 这一步出框之后用户有没有开过口 / 打过字
        self.main = MainThread()  # Agent 的 client tool 在 SDK 线程里跑，碰界面 / 截图要转到主线程
        self.agent: AgentSession | None = None
        self._enrolling = False
        self._stop_epoch = 0  # 每按一次 Stop +1：在这之前开始的 tool 调用 / 请求，结果回来一律丢掉
        self._talking = False  # 按键说话：正在收音
        self._pending_after_connect = None  # 硬停止后用户按键 / 打字 → 重新连接，连上后再做这件事
        self._turn = None  # 计时：{"end": 按结束键的时刻, ...}
        self.talk_timer = QTimer(self)  # 忘了按结束，30 秒自动结束
        self.talk_timer.setSingleShot(True)
        self.talk_timer.timeout.connect(lambda: self.end_talk("30 秒自动结束"))
        # 「这一步做完了」：点击 / 打字 / 画面变化，都走 _step_completed，一步只算一次
        self._step_done = True  # 还没有框时也算「做完了」，任何触发都忽略
        self._step_reason = None
        self._settle: dict | None = None  # 这一步做完了，正在等画面稳定再截图（_settle_tick）
        self._last_raw = None  # 轮询最近一帧（遮罩后）：画面变化触发时当「稳定」比较的起点
        self._n_filler = 0
        self._last_box: list[int] | None = None  # 上一个框：OCR 有多个候选时选离它近的
        # 每一步只认一个主信号（step_kind）：click = 点在框里；type = 回车；key = 有按键。用户说 done → Agent 带 note 调 tool。
        # 画面变化只在两种情况下算：没有辅助功能权限（监听不到点击 / 按键）、或者没有框（unsure 之后等用户自己弄好）；
        # 点在框外 NEAR_CLICK_PT 以内也算（框画偏了）。
        self._kind = "click"
        self._rebase = False  # 下一帧重取基准帧
        self.inputs = InputWatcher(self._on_click, self._on_key)
        self._inputs_ok = self.inputs.start()
        self.hotkey = RightOptionMonitor(self.toggle_talk)
        if not self.hotkey.start():
            self.panel.add_message("assistant", t("ax_needed"))
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
            self._user_turn_since_step = True
            self._remember("user", text)
            if self.agent.audio:
                self.agent.audio.new_user_turn()
            if not self.agent.send(text):
                self.panel.set_error(t("live_unavailable"))
            return
        if self.panel.live_box.isChecked() and self.agent is None:  # 硬停止后：用户打字 = 新任务，重新连接后再发
            self._remember("user", text)
            self._pending_after_connect = lambda: self.agent.send(text)
            self.set_live(True)
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
        if self.panel.live_box.isChecked():
            self.toggle_talk()
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
        dbg(f"_on_input_focus focused={focused} state={self.state} _typing={self._typing} 轮询在跑={self.poll.isActive()}")
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
        """面板上的「停止」按钮 = 硬停止：结束 Agent 会话、清框、停轮询 / 播放 / 收音。
        之前开始的 tool 调用和请求，结果回来一律丢掉；只有用户再按右 Option 或打字才会重新连接。"""
        self._stop_epoch += 1
        self._pending_after_connect = None
        self._set_talking(False)
        if self.agent:
            if self.agent.audio:
                self.agent.audio.silence_now()
            self.agent.stop()
            self.agent = None
            self.narrator.muted = False
            print(f"[agent] 硬停止 #{self._stop_epoch}：会话已结束", flush=True)
        self.narrator.stop()
        if self.recorder.recording:
            self.recorder.stop()
            self.panel.set_recording(False)
        self._chat_busy = False
        self.panel.set_busy(False)
        self._end_task(t("stopped_hard") if self.panel.live_box.isChecked() else t("stopped"))

    def shutdown(self):
        """退出前调用：停掉轮询、语音、录音，后面回来的请求结果都丢掉。"""
        self._token += 1
        self.poll.stop()
        self.app_timer.stop()
        self.inputs.stop()
        self.narrator.stop()
        if self.recorder.recording:
            self.recorder.stop()
        if self.agent:
            self.agent.stop()
        self.hotkey.stop()

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
                else:
                    dbg(f"丢掉一个过期的后台结果（token {token} ≠ 当前 {self._token}），handler={getattr(handler, '__name__', handler)}")
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
            r = ax_snap.snap_result(r, self._screen_wh)  # Dock 图标：换成 Accessibility 的精确框
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
                if r.get("snap"):
                    rec["snap"] = r["snap"]
                    rec["snap_rect_xywh"] = box_to_rect(r["box"], *self._screen_wh)
            (self._log_dir / f"{k}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
            brief = r.get("error") or (f"{r.get('action')} | {r.get('reply')}" if kind == "chat"
                                       else f"{r.get('status')} box={r.get('box')} | {r.get('instruction')}")
            print(f"[log] {k} {rec['seconds']}s {brief}", flush=True)
        except Exception as e:  # noqa: BLE001  记录失败不影响主流程
            print(f"[log] 记录失败：{e}")

    def _present_step(self, box: list[int], text: str, kind: str):
        """所有出框的地方都走这里（OCR 秒出 / Gemini / Gemini 更正 / tool）：画框，同一刻客户端自己念，
        并用背景消息告诉 Agent 进行到第几步。kind：ocr / gemini / correction / tool。"""
        self._show_step(box, text, kind)
        self._speak_step(text)
        if self.agent and self.agent.active:
            self.agent.context(f"[step] Step {self._sync_step} shown: {text}")

    def _speak_step(self, text: str, tag: int | None = None):
        """客户端念一步（或画面变化路径里的 done / unsure）：先停掉上一步还在念 / 还在下载的，
        再清掉 Agent 正在放的声音（并丢掉它在路上的，直到用户下一次开口）。"""
        tag = self._sync_step if tag is None else tag
        a = self.agent.audio if self.agent else None
        if a is not None:
            a.drop_for_client()
        if self._talking:  # 用户正按着键说话：不出声，免得被麦克风收进去（框照样画）
            sync.log(f"step {tag} voice skipped (user is talking)")
            self.narrator.stop()
            return
        # 刚回复过用户（打字模式）：这一步排在回复后面读；否则打断上一句没读完的
        self.narrator.say(text, interrupt=not self._speak_after_reply, tag=tag)
        self._speak_after_reply = False

    def _let_agent_speak(self):
        """tool 返回 DONE / UNSURE：这句由 Agent 说。停掉客户端在念的，别丢 Agent 的声音。"""
        self.narrator.stop()
        if self.agent and self.agent.audio:
            self.agent.audio.allow_agent()

    def _show_step(self, box: list[int], text: str, kind: str = "gemini"):
        rect = expand(box_to_rect(box, *self._screen_wh), HIGHLIGHT_PAD)
        self._last_box = list(box)
        self.overlay.show_target(rect, text)
        self.last_instruction = text
        self._sync_step = len(self.history) + 1
        self._user_turn_since_step = False
        sync.box(self._sync_step, kind, text)

    def _speed_log(self, how: str):
        if self._t_change is not None:
            ms = (time.perf_counter() - self._t_change) * 1000
            print(f"[step] {self._step_reason or '画面变化'} → 出框 {ms:.0f} ms（{how}）", flush=True)

    def _on_result(self, r: dict, source: str | None = None):
        dbg(f"_on_result status={r.get('status')} source={source} state={self.state} box={r.get('box')}")
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
            if prov:  # Gemini 更正 OCR 的框：打断 OCR 那句，念更正后的
                print(f"[speed] Gemini 更正：OCR 框 {prov['box']} → Gemini 框 {r['box']}", flush=True)
                self.panel.add_message("step", t("step_fixed", n=len(self.history) + 1, text=text))
                self._speak_after_reply = False
            self._present_step(r["box"], text, "correction" if prov else ("tool" if source == "tool" else "gemini"))
            self.panel.allow_retry(True)
            self._update_steps()
            if prov:
                self._wait(base_img=self._req_img)
            else:
                self._speed_log("等 Gemini")
                self.panel.add_step(len(self.history) + 1, text)
                self._wait()
            return
        if prov:  # OCR 秒出的框已经念了，Gemini 却说 done / unsure：马上停掉那句、清掉框
            print(f"[speed] OCR 框作废：Gemini 说 {status}（OCR 框 {prov['box']}「{prov['instruction']}」）", flush=True)
            self.narrator.stop()
            self.overlay.clear()
            self.last_instruction = None
            self._speak_after_reply = False
        if status == "done":
            # tool 要的（Agent 会祝贺）：客户端不念；画面变化路径里 Agent 不知道，客户端自己念
            self.overlay.clear()
            outro = wording.outro(self.task["app"], self.task["goal"], len(self.history), self.lang, text)
            self.panel.add_message("assistant", outro)
            self._remember("assistant", outro)
            self.panel.set_steps([h["instruction"] for h in self.history], None, [])
            if source == "tool":
                self._let_agent_speak()
            else:
                self._speak_step(outro, tag=len(self.history) + 1)
                if self.agent and self.agent.active:
                    self.agent.context(f"[step] Task done: {text or t('done')}")
            self._end_task(t("done"))
        else:  # unsure：比如要点的东西被挡住了 / 窗口没打开。画面一变就自动再问，也可以按「重试」
            self.overlay.clear()
            self.current, self.last_instruction = None, None
            msg = text or t("unsure")
            self.panel.add_message("assistant", msg)
            self._remember("assistant", msg)
            self.panel.set_status(t("unsure_hint"))
            self.panel.allow_retry(True)
            if source == "tool":  # Agent 会转告
                self._let_agent_speak()
            else:
                self._speak_step(msg, tag=len(self.history) + 1)
                if self.agent and self.agent.active:
                    self.agent.context(f"[step] Unsure: {msg}")
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
        self._step_done = False
        self._step_reason = None
        self._settle = None
        self._last_raw = None
        self.poll.setInterval(int(POLL_S * 1000))
        self._rebase = False
        cur = self.current or {}
        self._kind = step_kind(self.last_instruction, cur.get("expect")) if self.last_instruction else "none"
        self.poll.start()
        dbg(f"_wait：这一步靠 {self._kind} 判断完成（画面变化{'只做后备' if self._screen_only_backup() else '也算'}）")
        dbg(f"_wait：开始等画面变化 state={self.state} 基准帧={'用请求截图' if base_img is not None else '下一帧'} "
            f"轮询在跑={self.poll.isActive()} _typing={self._typing} 来源={getattr(self, '_result_source', None)}")

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
            dbg(f"_on_poll：state={self.state}，不检测")
            return
        if self._settle is not None:  # 这一步做完了：等画面稳定再截正式的图
            self._settle_tick()
            return
        if self._step_done:
            return
        try:
            raw = capture.to_small_gray(capture.grab())
        except Exception as e:  # noqa: BLE001
            self._on_error(api.ApiError(t("capture_failed", e=e)))
            return
        grew = self._grow_masks()
        if self._base_raw is None or self._rebase:  # 基准帧在高亮层显示之后取；点在框附近 / 忽略了一次变化后重取
            self._rebase = False
            self._base_raw = raw
            self.detector.reset(self._masked(raw))
            return
        if grew:  # 遮罩变大了：基准帧用同样的遮罩重算
            self.detector.reset(self._masked(self._base_raw))
        self._last_raw = self._masked(raw)  # 做之前的画面：等稳定时用来判断「画面变了没有」
        if self._kind in ("type", "key") and self._inputs_ok:  # 只认回车 / 按键（_on_key）或用户说 done，不看画面变化
            return
        fired = self.detector.feed(self._last_raw)
        if DEBUG_DETECT:
            area = self._screen_wh[0] * self._screen_wh[1]
            masked = min(1.0, sum(w * h for _, _, w, h in self._masks) / area)
            dbg(f"poll diff={self.detector.last_diff:.4f} 阈值={self.detector.threshold} 已变化={self.detector._changed} "
                f"稳定帧={self.detector._stable} 遮罩≈{masked * 100:.0f}% 遮罩数={len(self._masks)} _typing={self._typing} 触发={fired}")
        if not fired:
            return
        if not self._screen_only_backup():
            self._step_completed("screen_change")
        else:  # 加载、悬停、动画、下拉框……不是用户做完了这一步
            dbg("画面变了，但用户没点框 → 不算完成（重取基准帧）")
            self._rebase = True

    def _screen_only_backup(self) -> bool:
        """画面变化是不是只做后备：能监听到点击 / 按键、而且有框时是。"""
        return self._inputs_ok and self._kind != "none"

    def _step_completed(self, reason: str, detail: str | None = None):
        """点击 / 打字 / 画面变化 → 这一步做完了。一步只算一次，直到下一步的框出来。"""
        if self.state != WAITING or self._step_done:
            dbg(f"[step] 忽略重复触发 {reason}（state={self.state} 已完成={self._step_done}）")
            return
        self._step_done = True
        self._step_reason = reason
        self._t_change = time.perf_counter()
        print(f"[step] 完成（{reason}{'/' + detail if detail else ''}）：{self.last_instruction!r}", flush=True)
        # 不马上截图：菜单还在弹、窗口还在加载时截到旧画面，Gemini 会以为用户没点，又让点同一个（日志里 22 处）
        # 不管哪种信号，都从新的一帧开始比、至少等 SETTLE_MIN_S（画面变化触发那一帧不能当「稳定」的起点）
        # 先等画面「变了」（和做之前那一帧比），再等它稳定：网页点完要过一会儿才开始变，太早截到的还是旧画面
        self._settle = {"t0": time.monotonic(), "reason": reason, "n": 0, "min": SETTLE_MIN_S, "prev": None,
                        "base": self._last_raw, "changed": self._last_raw is None or reason == "screen_change"}  # 画面变化触发：已经变了
        self.poll.setInterval(int(SETTLE_POLL_S * 1000))
        if not self.poll.isActive():
            self.poll.start()

    def _settle_tick(self):
        """每 SETTLE_POLL_S 秒截一张缩略图：连续 2 张基本不变（diff ≤ CHANGE_THRESHOLD）就截正式的图；
        至少等 SETTLE_MIN_S；最多等 SETTLE_MAX_S（视频 / 动画一直在动也不能卡住）。"""
        st = self._settle
        el = time.monotonic() - st["t0"]
        try:
            self._grow_masks()
            raw = self._masked(capture.to_small_gray(capture.grab()))
        except Exception:  # noqa: BLE001  截不了缩略图：直接截正式的（出错在那边处理）
            raw = None
        st["n"] += 1
        d = _diff(raw, st["prev"]) if raw is not None and st["prev"] is not None else None
        st["prev"] = raw
        if raw is not None and not st["changed"] and _diff(raw, st["base"]) > CHANGE_THRESHOLD:
            st["changed"] = True
            st["t_changed"] = el
        if el >= SETTLE_MAX_S or raw is None:
            how = ("截图失败" if raw is None else "画面一直没变，到时间了" if not st["changed"] else "一直在动，到时间了")
        elif st["changed"] and d is not None and d <= CHANGE_THRESHOLD and el >= st["min"]:
            how = "稳定了" if st["base"] is None else f"{st.get('t_changed', 0) * 1000:.0f} ms 时开始变，之后稳定了"
        else:
            return
        print(f"[settle] {st['reason']}：等了 {el * 1000:.0f} ms，{how}（{st['n']} 帧，最后 diff={d if d is None else round(d, 4)}）", flush=True)
        self._settle = None
        self.poll.setInterval(int(POLL_S * 1000))
        self._on_screen_changed()

    def _on_click(self, x: float, y: float):
        """全局左键按下（用户点的是别的 App）。点在当前框里（或框外 NEAR_CLICK_PT 以内：框画偏了）= 这一步完成。
        OCR 秒出的框在 Gemini 核对回来之前也能点（_adopt_provisional）。"""
        prov = self.state == REQUESTING and self._provisional is not None
        if (self.state != WAITING and not prov) or self._step_done or self.overlay._rect is None:
            return
        r = self.overlay._rect
        rect = (r.x(), r.y(), r.width(), r.height())
        kind = step_kind(self._provisional["instruction"]) if prov else self._kind
        if kind == "type":  # 打字步骤：点输入框是打字的一部分，不算完成
            return
        if click_in_rect((x, y), rect, CLICK_EXTRA_PAD):
            reason = "click"
        elif near_rect((x, y), rect, CLICK_EXTRA_PAD, NEAR_CLICK_PT):
            reason = "click_near"
            dbg(f"点在框外附近 ({x:.0f}, {y:.0f})：算完成（框可能画偏了）")
        else:
            dbg(f"点在框外 ({x:.0f}, {y:.0f})，不算完成")
            return
        if prov:
            self._adopt_provisional()
        self._step_completed(reason)

    def _adopt_provisional(self):
        """用户在 Gemini 核对回来之前就照着 OCR 框做了：这一步就按 OCR 框算，丢掉还在路上的核对
        （它是按点之前的截图答的，回来也是旧的），直接走正常流程：等画面稳定 → 截图 → 下一步。"""
        prov, self._provisional = self._provisional, None
        print(f"[speed] 用户在 Gemini 核对前就点了 OCR 框「{prov['instruction']}」：不等核对，直接下一步", flush=True)
        self._token += 1  # 丢掉核对结果
        self.current = {"instruction": prov["instruction"], "box": prov["box"]}
        self.expect = None
        t_change = self._t_change
        self._wait()
        self._t_change = t_change
        try:  # 点之前的画面当基准（等稳定时要先看到画面变了）
            self._grow_masks()
            self._last_raw = self._masked(capture.to_small_gray(self._req_img))
        except Exception:  # noqa: BLE001
            self._last_raw = None

    def _on_key(self, is_return: bool):
        """全局按键：只看是不是回车，不记内容。打字步骤只认回车；快捷键步骤有按键就算。"""
        prov = self.state == REQUESTING and self._provisional is not None and self.overlay._rect is not None
        if (self.state != WAITING and not prov) or self._step_done:
            return
        kind = step_kind(self._provisional["instruction"]) if prov else self._kind
        reason = "enter" if kind == "type" and is_return else ("key" if kind == "key" else None)
        if reason is None:
            return
        if prov:
            self._adopt_provisional()
        self._step_completed(reason)

    def _on_screen_changed(self):
        dbg(f"_on_screen_changed ✅ 触发：state={self.state} last_instruction={self.last_instruction!r} next_guess={self._guess!r}")
        self._result_source = None
        if self._t_change is None:
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
            self._present_step(box, instr, "ocr")
            self._speed_log(f"OCR 秒出，识别 {ms:.0f} ms")
            self.panel.add_step(len(self.history) + 1, instr)
            self._update_steps()

        near, wh = self._last_box, self._screen_wh

        def locate():  # 顺序：AX Dock 匹配 → OCR
            t = time.perf_counter()
            box = ax_snap.dock_box(label, guess.get("instruction"), guess.get("box"), wh)
            return (box, (time.perf_counter() - t) * 1000) if box else ocr.locate(img, label, near=near)

        self._bg_free(locate, found, lambda e: print(f"[ocr] {_msg(e)}", flush=True))

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
            self.agent.first_audio.connect(self._on_first_audio)
            self.agent.voice_start.connect(self._on_agent_voice_start)
            self.panel.set_status(t("live_connecting"))
            self.agent.start()
        elif not on and self.agent:
            self.agent.stop()
            self.agent = None
            self.narrator.muted = False
            self.panel.set_live_state(False)
            self.panel.set_status(t("live_off"))

    def _on_agent_connected(self, engine_name: str):
        self.narrator.prefetch(list(FILLERS))  # 调 tool 时马上念的那两句：先生成好
        self.narrator.stop()  # 步骤由客户端念（不静音），对话由 Agent 说；两边不重叠见 _speak_step / _on_agent_voice_start
        self.panel.set_live_state(True)
        g = self.agent.gate
        self.panel.set_status(t("live_on", engine=engine_name))
        if g and not g.voiceprints:
            self.panel.add_message("assistant", t("no_voiceprint"))
        if g:
            g.filter_enabled = self.panel.filter_box.isChecked()
            g.push_to_talk = True  # 按键说话
        pending, self._pending_after_connect = self._pending_after_connect, None
        if pending:
            pending()
        elif not self.task:
            self.panel.add_message("assistant", t("ready_talk"))

    def _on_agent_failed(self, why: str):
        self.agent = None
        self._pending_after_connect = None
        self._set_talking(False)
        self.narrator.muted = False
        self.panel.set_live_state(False)
        self.panel.set_error(t("live_unavailable"))
        print(f"[agent] 退回打字模式：{why}", flush=True)

    def _timing(self, what: str):
        """计时：按结束键 → 转写 → 回复文字 → 第一段声音。"""
        if self._turn is None:
            return
        ms = (time.perf_counter() - self._turn["end"]) * 1000
        if what not in self._turn:
            self._turn[what] = ms
        if what == "first_audio":
            tr, rp = self._turn.get("transcript"), self._turn.get("reply")
            tools = ", ".join(f"{n} {d:.0f}ms" for n, d in self._turn.get("tools", [])) or "无"
            print(f"[timing] 按结束键 → 转写 {tr:.0f}ms → 回复文字 {rp:.0f}ms → 第一段声音 {ms:.0f}ms（tool：{tools}）"
                  if tr is not None and rp is not None else f"[timing] 按结束键 → 第一段声音 {ms:.0f}ms（转写 {tr}，回复 {rp}，tool：{tools}）",
                  flush=True)
            self._turn = None

    def _on_first_audio(self):
        self._timing("first_audio")

    def _on_agent_voice_start(self, gen: int):
        """Agent 开口（回答问题）：停掉客户端还在念的步骤。gen 过期 = 这之后客户端又开始念新的一步了，不停。"""
        sync.voice(self._sync_step if self.task is not None else None, "agent")
        a = self.agent.audio if self.agent else None
        if a is not None and gen == a.drop_gen and self.narrator.speaking():
            sync.log("client step speech stopped (agent is answering)")
            self.narrator.stop()

    def _on_agent_user(self, text: str):
        self._user_turn_since_step = True
        self._timing("transcript")
        print(f"[agent] 🧑 转写：{text!r}", flush=True)
        self.panel.add_message("user", text)
        self._remember("user", text)

    def _on_agent_reply(self, text: str):
        self._timing("reply")
        print(f"[agent] 🤖 回复：{text!r}", flush=True)
        if text.strip():
            self.panel.add_message("assistant", text)
            self._remember("assistant", text)

    def _on_voice_event(self, kind: str, score):
        if kind == "duck":
            self.panel.set_status(t("hearing"))
        elif kind == "reject":
            self.panel.set_status(t("ignored_voice"))
        elif kind == "accept":
            self.panel.set_status(t("listening_agent"))

    # ---- client tools（在 SDK 线程里调用）----
    def _filler(self):
        """主线程：Agent 一调用 tool 就念一句 "Let me look." / "One sec."（轮流），免得用户干等 4–6 秒。
        不丢 Agent 的声音（它的回答还在后面）；Agent 回答开始播放时，这句没念完就被 _on_agent_voice_start 停掉。"""
        if self._talking or not self.panel.voice_enabled():
            return
        text = FILLERS[self._n_filler % len(FILLERS)]
        self._n_filler += 1
        sync.log(f"filler {text!r} ({'cached' if self.narrator.cached(text) else 'not cached, fetching'})")
        self.narrator.say(text, interrupt=True)

    def tool_show_next_step(self, params: dict) -> str:
        epoch, t0p = self._stop_epoch, time.perf_counter()
        goal = (params.get("goal") or "").strip()
        note = (params.get("note") or "").strip() or None
        current = self.main.call(lambda: self.last_instruction if (
            self.task is not None and self.state == WAITING and self.overlay._rect is not None
            and not self._user_turn_since_step and not note) else None)
        if current:  # 框还在、用户没开口：Agent 不能自己换步骤，原样告诉它当前这一步（不截图、不出新框）
            sync.log(f"step {self._sync_step} agent called show_next_step by itself → kept the current step")
            return f"SHOWN: {current}"
        self.main.call(self._filler)
        t0 = time.time()
        try:
            img, payload = self.main.call(lambda: self._tool_prepare(goal, note))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            return msg if msg.startswith("STOPPED:") else f"ERROR: could not take a screenshot ({e})"
        try:
            r = api.next_step({**payload, "screenshot": capture.to_jpeg_b64(img)})
        except api.ApiError as e:
            self._log_turn("next", img, payload, {"error": e.message}, t0)
            self.main.call(lambda: self._on_error(e))
            return f"ERROR: {_english(e)}"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"
        r = ax_snap.snap_result(r, self._screen_wh)  # Dock 图标：换成 Accessibility 的精确框
        self._log_turn("next", img, payload, r, t0)
        self._tool_time("show_next_step", t0p)
        if epoch != self._stop_epoch:  # 等 Gemini 时用户按了 Stop：不画框、不念
            print("[agent] show_next_step 结果回来时已经按了 Stop，丢掉", flush=True)
            return "STOPPED: the user pressed Stop"
        if self.main.call(lambda: self.task is None or self.agent is None):
            return "STOPPED: the task already ended"
        self.main.call(lambda: self._on_result(r, source="tool"))
        return tool_reply(r)

    def _tool_time(self, name: str, t0: float):
        ms = (time.perf_counter() - t0) * 1000
        print(f"[timing] tool {name} {ms:.0f}ms", flush=True)
        if self._turn is not None:
            self._turn.setdefault("tools", []).append((name, ms))

    def _tool_prepare(self, goal: str, note: str | None):
        """主线程：决定是不是新任务，截图，组 next-step 请求。"""
        if self.agent is None:
            raise RuntimeError("STOPPED: the user pressed Stop")
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
        epoch, t0p = self._stop_epoch, time.perf_counter()
        self.main.call(self._filler)
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
        self._tool_time("look_at_screen", t0p)
        if epoch != self._stop_epoch:
            return "STOPPED: the user pressed Stop"
        return with_offer(r.get("reply") or "I'm not sure.", q)

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
        """旧开关（已隐藏）：现在固定是按键说话。"""

    # ---- 按键说话：右 Option / 麦克风按钮，按一下开始、再按一下结束 ----
    def toggle_talk(self):
        if self._talking:
            self.end_talk("按键")
        else:
            self.start_talk()

    def start_talk(self):
        if not self.panel.live_box.isChecked():
            self.toggle_mic_fallback()
            return
        if self.agent is None:  # 硬停止后 / 还没连：用户按键 = 新任务，先连接，连上后开始收音
            self._pending_after_connect = self.start_talk
            self.set_live(True)
            return
        if not self.agent.active or not self.agent.gate:
            return
        self._talking = True
        self._turn = None
        if self.agent.audio:
            self.agent.audio.sent_voice_frames, self.agent.audio.sent_voice_sumsq = 0, 0.0
        if self.agent.audio:  # Agent 正在说话 → 立刻停掉本地播放，用户说话期间丢掉它的声音
            self.agent.audio.silence_now()
        self.narrator.stop()  # 客户端在念的步骤也停掉：用户要说话
        self._set_talking(True)
        self.talk_timer.start(TALK_MAX_S * 1000)
        delay = _play_sound("Tink")  # 提示音不走回声消除：放完再开始发麦克风声音，免得被当成用户说的话
        # 不用定时器（按键回调来自 NSEvent，联调时 QTimer 没触发 → 一帧都没发出去）：立刻打开，但提示音放完前先不发
        gate = self.agent.gate
        gate.open_at = time.monotonic() + delay + 0.06
        gate.key_down = True
        print(f"[voice] 收音 开始（{delay + 0.06:.2f}s 后开始发声音）", flush=True)

    def end_talk(self, why: str = ""):
        if not self._talking:
            return
        self._talking = False
        self.talk_timer.stop()
        if self.agent and self.agent.gate:
            self.agent.gate.key_down = False  # 立刻改发静音帧：ElevenLabs 马上判断这句话说完了
        if self.agent and self.agent.audio:
            self.agent.audio.new_user_turn()  # 取消静音，也不再丢「客户端念步骤之后」到的声音：这是对用户这句话的回答
            self.agent.audio.await_first_audio = True
        self._turn = {"end": time.perf_counter()}
        self._set_talking(False)
        _play_sound("Pop")
        a = self.agent.audio if self.agent else None
        if a:
            import math
            n = a.sent_voice_frames
            rms = math.sqrt(a.sent_voice_sumsq / max(1, n * 512))
            db = 20 * math.log10(rms + 1e-9)
            print(f"[voice] 收音 结束（{why}）：发给 Agent {n * 0.032:.1f} 秒声音，音量 {db:.1f} dBFS"
                  f"{'  ⚠️ 几乎是静音——麦克风没收到声音？' if n and db < -50 else ''}{'  ⚠️ 一帧都没发出去' if not n else ''}", flush=True)
        else:
            print(f"[voice] 收音 结束（{why}）：⚠️ Agent 没连上", flush=True)

    def _set_talking(self, on: bool):
        if not on:
            self._talking = False
            self.talk_timer.stop()
            if self.agent and self.agent.gate:
                self.agent.gate.key_down = False
        self.panel.set_talking(on)
        if on:
            self.overlay.show_badge(t("listening_badge"))
        else:
            self.overlay.clear_badge()

    def toggle_mic_fallback(self):
        """实时语音关着时，麦克风按钮还是原来的「录一句 → 转文字 → /api/chat」。"""
        if self.recorder.recording:
            self._stop_recording()
            return
        self.narrator.stop()
        self.poll.stop()
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            self.panel.set_error(t("mic_failed", e=e))
            if self.state == WAITING:
                self._wait()
            return
        self.panel.set_recording(True)
        self.mic_timer.start(MIC_MAX_S * 1000)

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


_HOW_TO = re.compile(r"\b(how (do|can|should|would) (i|we|you)|how to|can (i|you)|could you|where (do|can|is)|is there a way|"
                     r"change|convert|create|make|add|remove|delete|open|save|export|install|set ?up|turn (on|off)|switch)\b",
                     re.IGNORECASE)


def with_offer(reply: str, question: str) -> str:
    """look_at_screen 只回答、不画框。如果用户问的是「怎么做成某件事」，在返回给 Agent 的内容后面加一句提示：
    回答完反问用户要不要一步步带他做；用户说好，就用这个目标调用 show_next_step。（纯解释性的问题不加。）"""
    if not _HOW_TO.search(question or ""):
        return reply
    return (f"{reply}\n\n[ShowMe note for the assistant] After you answer, ask the user in one short sentence: "
            f"\"Want me to show you step by step?\" If they say yes, call show_next_step with this as the goal: {question}")


FILLERS = ("Let me look.", "One sec.")


def tool_reply(r: dict) -> str:
    """show_next_step 返回给 Agent 的字符串（前缀以 docs/API.md 为准，一字不差）。"""
    status, text = r.get("status"), (r.get("instruction") or "").strip()
    if status == "next" and r.get("box"):
        return f"SHOWN: {text}"
    if status == "done":
        return f"DONE: {text}"
    if status == "unsure" or status == "next":  # next 却没有框：当成 unsure
        return f"UNSURE: {text}"
    return f"ERROR: unexpected reply from the step service ({status!r})"


def _english(e) -> str:
    """ERROR: 后面要英文。后端的错误说明可能是中文 → 换成通用英文说明。"""
    msg = getattr(e, "message", str(e)) or ""
    if msg and msg.isascii():
        return msg
    code = getattr(e, "status", None)
    return f"the step service failed (HTTP {code})" if code else "could not reach the step service"


def _play_sound(name: str) -> float:
    """播放系统提示音（NSSound），返回时长（秒）。出错返回 0。"""
    try:
        from AppKit import NSSound

        s = NSSound.soundNamed_(name)
        if s is None:
            return 0.0
        s.play()
        return float(s.duration())
    except Exception:  # noqa: BLE001
        return 0.0


def _different_goal(a: str, b: str) -> bool:
    """两个目标是不是明显不同（词重合很少）。Agent 每次调用时措辞会变，不能一变就当成新任务。"""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return len(wa & wb) / max(1, len(wa | wb)) < 0.3
