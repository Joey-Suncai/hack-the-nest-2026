"""检查线上 Agent 不会把上一个任务的 app 带进新请求（文字会话，重放 Spotify → Canvas 那段对话）。
运行：cd backend && uv run python scripts/check_agent_context.py [次数]
"""

import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import voice  # noqa: E402
from check_agent_dialog import turn  # noqa: E402

SHOWN = {"show_next_step": "SHOWN: Click the search icon.", "look_at_screen": "It is the search box."}


def calls(tools):
    return [p for t, p in tools if t == "show_next_step"]


def no_spotify(p):
    return "spotify" not in (p.get("goal", "") + p.get("app", "")).lower()


def is_canvas_app(p):
    app = p.get("app", "").lower()
    return "canvas" in p.get("goal", "").lower() and no_spotify(p) and "website" not in p.get("goal", "").lower() \
        and app in ("", "canvas") and "chrome" not in app


CASES = [
    ("Tell me how to add a friend in Spotify",
     lambda said, c: any("spotify" in p.get("app", "").lower() for p in c), "调 show_next_step，app=Spotify"),
    ("Walk me through how to use a canvas.",
     lambda said, c: (not c and bool(said)) or (bool(c) and all(no_spotify(p) for p in c)), "先问一句，或新任务且不带 Spotify"),
    ("I mean the Canvas app for my classes.",
     lambda said, c: bool(c) and all(is_canvas_app(p) for p in c), "goal 关于 Canvas，app=Canvas 或空，不是 Spotify / 网站"),
    ("Tell me how to use the Canvas software.",
     lambda said, c: bool(c) and all(is_canvas_app(p) for p in c), "原生 app，不是 website"),
    ("Now show me how to follow Ben in Spotify",
     lambda said, c: any("spotify" in (p.get("app", "") + p.get("goal", "")).lower() for p in c), "对照：Spotify 没问题"),
]


async def run() -> int:
    url = voice.agent_session()["signed_url"]
    ok = 0
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))
        while json.loads(await ws.recv()).get("type") != "conversation_initiation_metadata":
            pass
        for text, test, want in CASES:
            said, tools = await turn(ws, {"type": "user_message", "text": text}, SHOWN)
            good = test(said, calls(tools))
            ok += good
            print(f"{'✅' if good else '❌'} 用户：{text}  （期望：{want}）")
            for t, p in tools:
                print(f"     tool {t}: goal={p.get('goal')!r} app={p.get('app')!r} note={p.get('note')!r}")
            print(f"     Agent 说：{said or '（没说话）'}")
    print(f"{ok}/{len(CASES)} 条通过\n")
    return ok


if __name__ == "__main__":
    for i in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1):
        print(f"=== 第 {i + 1} 次 ===")
        asyncio.run(run())
