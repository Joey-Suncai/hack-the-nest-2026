"""检查线上 Agent 是否守「步骤由客户端念、Agent 只负责对话」的规则（文字会话）。
运行：cd backend && uv run python scripts/check_agent_dialog.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import voice  # noqa: E402

STEP1 = "Click Clone Git Repository under Start on the Welcome page."
STEP2 = "Click Clone from URL in the dropdown."


async def turn(ws, send: dict, tool_results: dict, wait=4.0):
    """发一条消息，收集这一轮 Agent 说的话和调用的 tool。"""
    await ws.send(json.dumps(send))
    said, tools, last = [], [], time.perf_counter()
    while time.perf_counter() - last < wait:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3))
        except asyncio.TimeoutError:
            continue
        t = msg.get("type")
        if t == "ping":
            await ws.send(json.dumps({"type": "pong", "event_id": msg["ping_event"]["event_id"]}))
        elif t == "client_tool_call":
            c = msg["client_tool_call"]
            tools.append((c["tool_name"], c["parameters"]))
            await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": c["tool_call_id"],
                                      "result": tool_results.get(c["tool_name"], "OK"), "is_error": False}))
            last = time.perf_counter()
        elif t == "agent_response":
            said.append(msg["agent_response_event"]["agent_response"])
            last = time.perf_counter()
        elif t == "audio":
            last = time.perf_counter()
    return said, tools


def check(name, ok, said, tools):
    print(f"{'✅' if ok else '❌'} {name}\n     Agent 说：{said or '（没说话）'}  tool：{tools or '-'}")
    return ok


async def main():
    url = voice.agent_session()["signed_url"]
    results = []
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))
        while json.loads(await ws.recv()).get("type") != "conversation_initiation_metadata":
            pass
        shown1 = {"show_next_step": f"SHOWN: {STEP1}", "look_at_screen": "It is the search box at the top."}
        said, tools = await turn(ws, {"type": "user_message", "text": "Help me clone https://github.com/octocat/Hello-World in VS Code"}, shown1)
        results.append(check("开始任务 → 调 show_next_step，收到 SHOWN 后不重复步骤",
                             any(t == "show_next_step" for t, _ in tools) and not any("Clone Git" in s for s in said[1:] + said[-1:] if s), said, tools))
        said, tools = await turn(ws, {"type": "contextual_update", "text": f"[step] Step 1 shown: {STEP1}"}, {})
        results.append(check("contextual update [step] → 不回复", not said and not tools, said, tools))
        said, tools = await turn(ws, {"type": "user_message", "text": f"[step] Step 2 shown: {STEP2}"}, {})
        results.append(check("用户消息形式的 [step] → 不念出来", not any("Clone from URL" in s for s in said) and not tools, said, tools))
        shown2 = {"show_next_step": f"SHOWN: {STEP2}"}
        said, tools = await turn(ws, {"type": "user_message", "text": "Oops, I clicked the wrong thing."}, shown2)
        results.append(check("点错了 → 调 show_next_step 带 note，不重复步骤",
                             any(t == "show_next_step" and p.get("note") for t, p in tools) and not any("Clone from URL" in s for s in said), said, tools))
        said, tools = await turn(ws, {"type": "user_message", "text": "What should I click next?"}, shown2)
        results.append(check("问「下一步点哪」→ 不自己猜，去调 show_next_step",
                             any(t == "show_next_step" for t, _ in tools) and not any("Clone from URL" in s for s in said), said, tools))
        said, tools = await turn(ws, {"type": "user_message", "text": "What does clone mean?"}, shown1)
        results.append(check("问概念 → 自己简短回答", bool(said), said, tools))
        done = {"show_next_step": "DONE: The repository is cloned and open in VS Code."}
        said, tools = await turn(ws, {"type": "user_message", "text": "Okay, I did it."}, done)
        results.append(check("返回 DONE → 简短祝贺", bool(said), said, tools))
        stopped = {"show_next_step": "STOPPED: The user stopped the task."}
        said, tools = await turn(ws, {"type": "user_message", "text": "Next step please."}, stopped)
        results.append(check("返回 STOPPED → 简短回应", bool(said), said, tools))
    print(f"\n{sum(results)}/{len(results)} 条通过")


if __name__ == "__main__":
    asyncio.run(main())
