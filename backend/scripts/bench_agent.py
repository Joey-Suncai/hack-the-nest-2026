"""测 ElevenLabs Agent 的反应时间：连线上 Agent，在同一个会话里依次发 5 句文字，记录
「发出 → 第一段语音」「发出 → 调用 tool」「tool 结果返回 → 第一段语音」。
文字输入跳过了语音识别和「判断用户说完」，对应客户端已经决定用户说完之后的部分。
tool 由本脚本立刻返回固定结果，所以测的是 Agent 自己的耗时，不含截图和 /api/next-step。

运行：cd backend && uv run python scripts/bench_agent.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import voice  # noqa: E402

LINES = [
    "Help me clone https://github.com/octocat/Hello-World in VS Code",
    "What is that button at the top of my screen?",
    "Okay, done.",
    "[screen changed] Next: Click Clone from URL in the dropdown.",
    "Thanks!",
]
TOOL_RESULTS = {
    "show_next_step": "Click the input box at the top of the window.",
    "look_at_screen": "That is the search box. You can type a command or file name there.",
}
QUIET_S = 2.0  # 最后一段语音之后安静这么久，算这一轮说完


async def run_line(ws, text: str) -> dict:
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "user_message", "text": text}))
    r = {"text": text, "first_audio": None, "tool": None, "tool_call": None, "after_tool_audio": None, "said": []}
    tool_sent_at = None
    last_activity = time.perf_counter()
    while True:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3))
        except asyncio.TimeoutError:
            if r["first_audio"] is not None and time.perf_counter() - last_activity > QUIET_S:
                return r
            if time.perf_counter() - t0 > 30:
                return r
            continue
        now = time.perf_counter()
        t = msg.get("type")
        if t == "ping":
            await ws.send(json.dumps({"type": "pong", "event_id": msg["ping_event"]["event_id"]}))
        elif t == "audio":
            last_activity = now
            if r["first_audio"] is None:
                r["first_audio"] = now - t0
            if tool_sent_at is not None and r["after_tool_audio"] is None:
                r["after_tool_audio"] = now - tool_sent_at
        elif t == "client_tool_call":
            c = msg["client_tool_call"]
            r["tool"], r["tool_call"] = c["tool_name"], now - t0
            await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": c["tool_call_id"],
                                      "result": TOOL_RESULTS.get(c["tool_name"], "OK"), "is_error": False}))
            tool_sent_at = time.perf_counter()
            last_activity = now
        elif t == "agent_response":
            r["said"].append(msg["agent_response_event"]["agent_response"])
            last_activity = now


async def main():
    url = voice.agent_session()["signed_url"]
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))
        while json.loads(await ws.recv()).get("type") != "conversation_initiation_metadata":
            pass
        rows = [await run_line(ws, line) for line in LINES]
    fmt = lambda x: f"{x:4.2f}s" if x is not None else "  -  "  # noqa: E731
    print(f"{'用户说':<46} {'首段语音':>8} {'调tool':>7} {'tool后语音':>9}  tool / Agent 说的")
    for r in rows:
        print(f"{r['text'][:44]:<46} {fmt(r['first_audio']):>8} {fmt(r['tool_call']):>7} {fmt(r['after_tool_audio']):>9}"
              f"  {r['tool'] or '-'} / {' | '.join(r['said'])[:90]}")
    fa = [r["first_audio"] for r in rows if r["first_audio"] is not None]
    print(f"\n首段语音平均 {sum(fa) / len(fa):.2f}s（{len(fa)}/{len(rows)} 句有语音）")


if __name__ == "__main__":
    asyncio.run(main())
