"""实时语音（ElevenLabs Agent）：连接、两个 client tool、把对话显示到面板。

- signed_url 来自后端 /api/agent-session（客户端没有 ElevenLabs key）；SDK 只会用 key 自己取，
  所以继承 Conversation 改写 _get_signed_url（elevenlabs==2.78.0 已锁版本）
- 麦克风 / 扬声器走 audio_engine.FilteredAgentAudio：回声消除 → VAD → 声纹 → 先压低音量再确认
- client tool 在 SDK 自己的线程池里执行（同步 handler 可以阻塞）；要碰界面 / 截图的部分用 MainThread.call 转到 Qt 主线程
- 连不上 / 中途断开 → 发 failed 信号，控制器退回「打字 + /api/chat + /api/speak」
"""

import threading
import urllib.parse

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from showme import api
from showme.voice_filter import Gate, SpeakerModel, load_voiceprints


class MainThread(QObject):
    """在别的线程里调用 call(fn)：fn 在 Qt 主线程执行，等它做完再返回结果。"""

    _sig = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._sig.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, job):
        fn, done, box = job
        try:
            box["result"] = fn()
        except Exception as e:  # noqa: BLE001
            box["error"] = e
        done.set()

    def call(self, fn, timeout: float = 15):
        done, box = threading.Event(), {}
        self._sig.emit((fn, done, box))
        if not done.wait(timeout):
            raise TimeoutError("界面线程没有响应")
        if "error" in box:
            raise box["error"]
        return box.get("result")


def _signed_conversation_class():
    from elevenlabs import __version__ as sdk_version
    from elevenlabs.conversational_ai.conversation import Conversation

    class SignedUrlConversation(Conversation):
        """用后端给的 signed_url 连接，客户端不需要 API key。"""

        def __init__(self, *a, signed_url: str, **kw):
            self._backend_signed_url = signed_url
            super().__init__(*a, **kw)

        def _get_signed_url(self):
            parsed = urllib.parse.urlparse(self._backend_signed_url)
            params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            params += [("source", "python_sdk"), ("version", sdk_version)]
            return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(params, quote_via=urllib.parse.quote)))

    return SignedUrlConversation


class AgentSession(QObject):
    connected = pyqtSignal(str)  # 录放方式（有没有回声消除）
    failed = pyqtSignal(str)  # 连不上 / 断开（给用户看的原因）
    user_said = pyqtSignal(str)
    agent_said = pyqtSignal(str)
    voice_event = pyqtSignal(str, object)  # ("duck" | "accept" | "reject", 分数)
    first_audio = pyqtSignal()  # 用户说完后，Agent 的第一段声音开始播放（计时用）

    def __init__(self, tools: dict, lang: str = "en"):
        """tools：{"show_next_step": fn(params) -> str, "look_at_screen": fn(params) -> str}，在 SDK 线程里调用。"""
        super().__init__()
        self.tools, self.lang = tools, lang
        self.conv = None
        self.audio = None
        self.gate: Gate | None = None
        self._closing = False

    @property
    def active(self) -> bool:
        return self.conv is not None and not self._closing

    def start(self):
        """在后台线程连接，不卡界面。"""
        threading.Thread(target=self._connect, name="agent-connect", daemon=True).start()

    def _connect(self):
        try:
            from elevenlabs.client import ElevenLabs
            from elevenlabs.conversational_ai.conversation import ClientTools

            from showme.audio_engine import FilteredAgentAudio

            sess = api.agent_session()
            prints = load_voiceprints()
            scorer = SpeakerModel().embed if prints else None
            self.gate = Gate(scorer=scorer, voiceprint=prints or None)
            print(f"[voice] 声纹：{list(prints) or '没录（只用 VAD + 时长）'}，阈值 {self.gate.threshold}", flush=True)
            self.gate.push_to_talk = True  # 按键说话：只有按了右 Option 才发麦克风声音，不做 VAD / 声纹
            self.audio = FilteredAgentAudio(self.gate, on_event=lambda k, s: self.voice_event.emit(k, s))
            self.audio.on_first_audio = self.first_audio.emit
            tools = ClientTools()
            for name, fn in self.tools.items():
                tools.register(name, self._wrap(name, fn))
            Conv = _signed_conversation_class()
            self.conv = Conv(
                ElevenLabs(api_key="not-used-client-side"),  # 不会用到：URL 来自后端
                sess["agent_id"],
                requires_auth=True,
                signed_url=sess["signed_url"],
                audio_interface=self.audio,
                client_tools=tools,
                callback_agent_response=lambda t: self.agent_said.emit(t),
                callback_user_transcript=lambda t: self.user_said.emit(t),
                callback_disconnect=self._on_disconnect,
            )
            self.conv.start_session()
            # start_session 在 SDK 自己的线程里启动录放：等它真的起来再报「已连接」
            if not self.audio.started.wait(10):
                raise RuntimeError("麦克风 / 扬声器 10 秒内没启动")
            self.connected.emit(self.audio.engine.name)
        except api.ApiError as e:
            print(f"[agent] 拿不到 agent-session：{e.message}（HTTP {e.status}）", flush=True)
            self.conv = None
            self.failed.emit(e.message)
        except Exception as e:  # noqa: BLE001  任何问题都退回打字模式，不能崩
            print(f"[agent] 连接失败：{e!r}", flush=True)
            conv, self.conv = self.conv, None
            if conv is not None:  # 会话可能已经起来了：关掉，免得后台还有一个 Agent 在听
                self._closing = True
                try:
                    conv.end_session()
                except Exception:  # noqa: BLE001
                    pass
            self.failed.emit(str(e))

    def _wrap(self, name, fn):
        def handler(params: dict):
            print(f"[agent] 🛠 {name} {({k: v for k, v in params.items() if k != 'tool_call_id'})}", flush=True)
            try:
                out = fn(params)
            except Exception as e:  # noqa: BLE001
                out = f"ERROR: {e}"
            print(f"[agent] 🛠 {name} → {out!r}", flush=True)
            return out

        return handler

    def _on_disconnect(self, details):
        if self._closing:
            return
        print(f"[agent] 连接断开：{details}", flush=True)
        self.conv = None
        self.failed.emit(f"disconnected ({getattr(details, 'reason', details)})")

    def send(self, text: str) -> bool:
        """发一条用户消息（打字输入、[screen changed] 通知）。"""
        if not self.active:
            return False
        try:
            self.conv.send_user_message(text)
            print(f"[agent] → {text!r}", flush=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[agent] 发送失败：{e}", flush=True)
            return False

    def hush(self):
        """用户按了 Stop：本地马上安静，并告诉 Agent 停下来等用户再开口（不断开连接）。"""
        if self.audio:
            self.audio.silence_now()
        self.send("[stopped] The user pressed Stop. Stop talking and wait until the user speaks again.")

    def unhush(self):
        if self.audio:
            self.audio.muted = False

    def stop(self):
        self._closing = True
        conv, self.conv = self.conv, None
        if conv is None:
            return

        def close():
            try:
                conv.end_session()
                conv.wait_for_session_end()
            except Exception as e:  # noqa: BLE001
                print(f"[agent] 结束会话出错：{e}", flush=True)

        threading.Thread(target=close, name="agent-close", daemon=True).start()
