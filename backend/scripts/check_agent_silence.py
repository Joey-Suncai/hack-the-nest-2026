"""检查线上 Agent 在长时间安静时不会主动开口、也不会断线（模拟按键说话：一直发静音帧）。
运行：cd backend && uv run python scripts/check_agent_silence.py [秒数，默认 130]
"""

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import voice  # noqa: E402

SILENCE = base64.b64encode(b"\x00\x00" * 4000).decode()  # 16 kHz、0.25 秒的静音 PCM


async def main(seconds: float):
    url = voice.agent_session()["signed_url"]
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))
        while json.loads(await ws.recv()).get("type") != "conversation_initiation_metadata":
            pass
        await ws.send(json.dumps({"type": "user_message", "text": "Hi, I'm here."}))
        t_reply = None
        spoke_later, t0 = [], time.perf_counter()

        async def feed():
            while True:
                await ws.send(json.dumps({"user_audio_chunk": SILENCE}))
                await asyncio.sleep(0.25)

        feeder = asyncio.create_task(feed())
        try:
            while time.perf_counter() - t0 < seconds:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                except asyncio.TimeoutError:
                    continue
                t = msg.get("type")
                if t == "ping":
                    await ws.send(json.dumps({"type": "pong", "event_id": msg["ping_event"]["event_id"]}))
                elif t == "agent_response":
                    text = msg["agent_response_event"]["agent_response"]
                    now = time.perf_counter() - t0
                    if t_reply is None:
                        t_reply = now
                        print(f"[{now:5.1f}s] 第一次回复：{text}")
                    else:
                        spoke_later.append((now, text))
                        print(f"[{now:5.1f}s] ⚠️ 安静时又开口：{text}")
            print(f"\n安静 {seconds - (t_reply or 0):.0f} 秒：主动开口 {len(spoke_later)} 次，连接{'仍然在线 ✅' if ws.state.name == 'OPEN' else '已断开 ❌'}")
        except websockets.ConnectionClosed as e:
            print(f"❌ 连接在 {time.perf_counter() - t0:.0f}s 被关闭：{e}")
        finally:
            feeder.cancel()


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 130))
