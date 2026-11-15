"""创建 / 更新 ShowMe 的 ElevenLabs Agent（实时语音对话）。

运行：cd backend && uv run python scripts/create_agent.py [--write-env]
- 两个 client tool（show_next_step、look_at_screen）按名字查找，存在就更新，不存在就创建
- .env 里有 ELEVENLABS_AGENT_ID 就更新这个 agent，没有就新建；--write-env 把新 agent id 写进 .env
- 系统提示：backend/prompts/agent.txt；LLM：AGENT_LLM（默认 gemini-3.6-flash）；声音：ELEVENLABS_VOICE_ID
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / ".env"
load_dotenv(ENV)
API = "https://api.elevenlabs.io/v1/convai"
KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
HEADERS = {"xi-api-key": KEY}

TOOLS = [
    {
        "type": "client",
        "name": "show_next_step",
        "description": "Take a screenshot of the user's screen, work out the next thing they should click to reach the goal, "
                       "highlight it on screen, and return the instruction to say. Call it when a task starts, and whenever "
                       "the user says they finished, skipped, clicked the wrong thing, or are stuck.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What the user wants to accomplish, short and specific (keep URLs, file names, app names)."},
                "note": {"type": "string", "description": "Optional: what the user just said about their progress, e.g. 'user clicked the wrong thing'."},
            },
            "required": ["goal"],
        },
        "expects_response": True,
        "response_timeout_secs": 30,
    },
    {
        "type": "client",
        "name": "look_at_screen",
        "description": "Take a screenshot of the user's screen and answer their question about what is on it.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The user's question about their screen."}},
            "required": ["question"],
        },
        "expects_response": True,
        "response_timeout_secs": 30,
    },
]


def check(r: httpx.Response) -> dict:
    if r.status_code >= 300:
        sys.exit(f"ElevenLabs {r.request.method} {r.request.url.path} → {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def upsert_tools(client: httpx.Client) -> list[str]:
    existing = {}
    for t in check(client.get(f"{API}/tools")).get("tools", []):
        name = (t.get("tool_config") or {}).get("name")
        if name:
            existing[name] = t["id"]
    ids = []
    for cfg in TOOLS:
        if cfg["name"] in existing:
            tid = existing[cfg["name"]]
            check(client.patch(f"{API}/tools/{tid}", json={"tool_config": cfg}))
            print(f"更新 tool {cfg['name']}：{tid}")
        else:
            tid = check(client.post(f"{API}/tools", json={"tool_config": cfg}))["id"]
            print(f"创建 tool {cfg['name']}：{tid}")
        ids.append(tid)
    return ids


def agent_body(tool_ids: list[str]) -> dict:
    prompt = (ROOT / "backend" / "prompts" / "agent.txt").read_text(encoding="utf-8")
    # 英文 Agent 只能用 turbo / flash v2（v2_5 会被拒）
    tts = {"model_id": os.getenv("AGENT_TTS_MODEL", "eleven_flash_v2")}
    if os.getenv("ELEVENLABS_VOICE_ID", "").strip():
        tts["voice_id"] = os.getenv("ELEVENLABS_VOICE_ID").strip()
    return {
        "name": "ShowMe",
        "conversation_config": {
            "agent": {
                "first_message": "",
                "language": "en",
                "prompt": {
                    "prompt": prompt,
                    "llm": os.getenv("AGENT_LLM", "gemini-3.6-flash"),
                    "temperature": 0.3,
                    "tool_ids": tool_ids,
                },
            },
            "tts": tts,
        },
        "platform_settings": {
            # 允许客户端开会话时覆盖语言（中文）和开场白
            "overrides": {"conversation_config_override": {"agent": {"language": True, "first_message": True}}},
        },
    }


def write_env(agent_id: str):
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if not ln.startswith("ELEVENLABS_AGENT_ID=")]
    lines.append(f"ELEVENLABS_AGENT_ID={agent_id}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {ENV}")


def main():
    if not KEY:
        sys.exit("先在 .env 里填 ELEVENLABS_API_KEY")
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        tool_ids = upsert_tools(client)
        body = agent_body(tool_ids)
        agent_id = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
        if agent_id:
            check(client.patch(f"{API}/agents/{agent_id}", json=body))
            print(f"更新 agent：{agent_id}")
        else:
            agent_id = check(client.post(f"{API}/agents/create", json=body))["agent_id"]
            print(f"创建 agent：{agent_id}")
        a = check(client.get(f"{API}/agents/{agent_id}"))
        cc = a.get("conversation_config", {})
        p = cc.get("agent", {}).get("prompt", {})
        print(f"核对：llm={p.get('llm')} tools={len(p.get('tool_ids') or [])} tts={cc.get('tts', {}).get('model_id')}")
    if "--write-env" in sys.argv:
        write_env(agent_id)
    else:
        print(f"把这一行加进 .env：ELEVENLABS_AGENT_ID={agent_id}")


if __name__ == "__main__":
    main()
