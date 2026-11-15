"""实时语音小实验（ElevenLabs Agents）。用完就丢，正式版在 showme/agent.py。

运行（一定要戴耳机，不然 Agent 会听到自己的声音）：
  cd client && SHOWME_TOKEN=<口令> SHOWME_API=http://<后端>:8000 uv run python spike_agent.py

  按回车  → 用 send_user_message 发「[screen changed] Next: Click Chart in the toolbar.」（应该马上念出来）
  （没有键盘输入时自动模式：Agent 调 show_next_step 8 秒后自动发）
  输入 c 回车 → 用 send_contextual_update 发同一句（对比：按 SDK 源码说明，它不会让 Agent 开口）
  输入 q 回车 → 结束

要验证：① 它说话时你一开口它就停  ② 说「帮我在 Numbers 里做柱状图」会调用 show_next_step  ③ 按回车它马上念下一步

SDK 写法依据（elevenlabs==2.78.0）：
  - 文档 https://elevenlabs.io/docs/agents-platform/libraries/python ：Conversation / ClientTools / start_session / end_session
  - SDK 源码 conversation.py：AudioInterface 的 4 个方法（16 kHz 16-bit 单声道 PCM）、send_user_message、
    send_contextual_update（"without directly prompting the agent"）、_get_signed_url（只会用 API key 去取）
  - 客户端不能有 API key → 继承 Conversation，_get_signed_url 改成用后端 /api/agent-session 给的 signed_url
"""

import os
import queue
import sys
import threading
import time
import urllib.parse

import httpx
import sounddevice as sd
from elevenlabs import __version__ as sdk_version
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import AudioInterface, ClientTools, Conversation

API = os.getenv("SHOWME_API", "http://localhost:8000")
RATE = 16000
CHUNK = 4000  # 250 ms，SDK 推荐
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.2f}s]", *a, flush=True)


class SoundDeviceAudio(AudioInterface):
    """用 sounddevice 实现 SDK 的音频接口（不用 pyaudio / brew portaudio）。"""

    def start(self, input_callback):
        self._out_q: queue.Queue[bytes] = queue.Queue()
        self._buf = b""
        self._in = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK,
                                     callback=lambda data, frames, t, status: input_callback(bytes(data)))
        self._out = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=800,
                                       callback=self._play)
        self._in.start()
        self._out.start()

    def _play(self, outdata, frames, t, status):
        need = len(outdata)
        while len(self._buf) < need:
            try:
                self._buf += self._out_q.get_nowait()
            except queue.Empty:
                break
        chunk, self._buf = self._buf[:need], self._buf[need:]
        outdata[: len(chunk)] = chunk
        outdata[len(chunk):] = b"\x00" * (need - len(chunk))

    def stop(self):
        for s in (self._in, self._out):
            try:
                s.stop()
                s.close()
            except Exception:  # noqa: BLE001
                pass

    def output(self, audio: bytes):
        self._out_q.put(audio)

    def interrupt(self):
        log("⚡ interrupt：用户开口了，清空播放缓冲")
        self._buf = b""
        try:
            while True:
                self._out_q.get_nowait()
        except queue.Empty:
            pass


class SignedUrlConversation(Conversation):
    """用后端给的 signed_url 连接，客户端不需要 API key。"""

    def __init__(self, *a, signed_url: str, **kw):
        self._backend_signed_url = signed_url
        super().__init__(*a, **kw)

    def _get_signed_url(self):
        # 和 SDK 原版一样附上 source / version 参数，只是 URL 来自我们的后端
        parsed = urllib.parse.urlparse(self._backend_signed_url)
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        params += [("source", "python_sdk"), ("version", sdk_version)]
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(params, quote_via=urllib.parse.quote)))


def main():
    log(f"elevenlabs SDK {sdk_version}，后端 {API}")
    try:
        token = os.getenv("SHOWME_TOKEN")  # 口令只从环境变量读
        r = httpx.get(f"{API}/api/agent-session", timeout=10, headers={"X-ShowMe-Token": token} if token else {})
    except httpx.HTTPError as e:
        sys.exit(f"连不上后端：{e!r}")
    if r.status_code != 200:
        sys.exit(f"/api/agent-session → HTTP {r.status_code}: {r.text[:200]}（503 = 后端没配实时语音）")
    sess = r.json()
    log("拿到 agent_id =", sess.get("agent_id"), "| signed_url 前缀 =", (sess.get("signed_url") or "")[:48], "…")

    tools = ClientTools()
    calls = []

    def show_next_step(params: dict):
        calls.append(("show_next_step", params, time.time()))
        log("🛠  Agent 调用了 show_next_step", params)
        return "Step 1: Click Chart in the toolbar at the top of the window."

    def look_at_screen(params: dict):
        calls.append(("look_at_screen", params, time.time()))
        log("🛠  Agent 调用了 look_at_screen", params)
        return "The user is looking at a Numbers spreadsheet with a table of numbers."

    tools.register("show_next_step", show_next_step)
    tools.register("look_at_screen", look_at_screen)

    conv = SignedUrlConversation(
        ElevenLabs(api_key="not-used-client-side"),  # 不会用到：URL 来自后端
        sess["agent_id"],
        requires_auth=True,
        signed_url=sess["signed_url"],
        audio_interface=SoundDeviceAudio(),
        client_tools=tools,
        callback_agent_response=lambda t: log("🤖 Agent:", t),
        callback_agent_response_correction=lambda orig, cut: log("✂️  被打断，实际只说到:", cut),
        callback_user_transcript=lambda t: log("🧑 你:", t),
        callback_latency_measurement=lambda ms: log(f"⏱  延迟 {ms} ms"),
        callback_disconnect=lambda d: log("连接断开:", d),
    )
    conv.start_session()
    msg = "[screen changed] Next: Click Chart in the toolbar."
    if sys.stdin.isatty():
        log("已连接。开始说话吧（戴耳机）。回车 = 发 [screen changed]，c = 用 contextual update 发，q = 退出")
        while True:
            cmd = sys.stdin.readline().strip().lower()
            if cmd == "q":
                break
            try:
                if cmd == "c":
                    conv.send_contextual_update(msg)
                    log("→ send_contextual_update:", msg)
                else:
                    conv.send_user_message(msg)
                    log("→ send_user_message:", msg)
            except Exception as e:  # noqa: BLE001
                log("发送失败:", e)
    else:
        # 自动模式（从后台启动，没有键盘输入）：Agent 第一次调 show_next_step 后 8 秒，自动发 [screen changed]
        total = int(os.getenv("SPIKE_SECONDS", "120"))
        log(f"已连接（自动模式，{total} 秒后结束）。开始说话吧（戴耳机）")
        sent = False
        deadline = time.time() + total
        while time.time() < deadline:
            time.sleep(0.5)
            first_tool = next((t for name, _, t in calls if name == "show_next_step"), None)
            if not sent and first_tool and time.time() - first_tool > 8:
                try:
                    conv.send_user_message(msg)
                    log("→ send_user_message:", msg, "（看它多久开口念）")
                except Exception as e:  # noqa: BLE001
                    log("发送失败:", e)
                sent = True
    conv.end_session()
    conv.wait_for_session_end()
    log("结束。client tool 调用记录:", calls or "（没有调用）")


if __name__ == "__main__":
    threading.excepthook = lambda a: log("线程异常:", a.exc_value)
    main()
