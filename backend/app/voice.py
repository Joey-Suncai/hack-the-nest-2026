"""ElevenLabs 的唯一入口：把一句话转成 mp3。

没配 ELEVENLABS_API_KEY 或 GEMINI_MOCK=1 时返回 None（接口回 204，客户端跳过播放）。
key 每次调用时读，方便测试 monkeypatch。
"""

from __future__ import annotations

import os

import httpx

from app import gemini

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs 预置声音 Rachel；multilingual 模型也能读中文
TIMEOUT_S = 15
MAX_CHARS = 300


def speak(text: str, lang: str = "zh") -> bytes | None:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if gemini.MOCK or not key:
        return None
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICE_ID
    model = os.getenv("ELEVENLABS_MODEL", "").strip() or "eleven_multilingual_v2"
    try:
        r = httpx.post(
            API_URL.format(voice_id=voice_id),
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": model},
            timeout=TIMEOUT_S,
        )
    except httpx.TimeoutException:
        raise gemini.GeminiError("语音服务超时了，请稍后再试", 504)
    except httpx.HTTPError as e:
        print(f"[voice] network error: {type(e).__name__}: {e}")
        raise gemini.GeminiError("连不上语音服务，请检查网络", 504)
    if r.status_code != 200 or not r.content:
        print(f"[voice] elevenlabs {r.status_code}: {r.text[:300]}")
        msg = {401: "ElevenLabs API key 无效", 402: "ElevenLabs 额度用完了", 429: "ElevenLabs 调用太频繁 / 额度用完了"}
        raise gemini.GeminiError(msg.get(r.status_code, f"语音服务拒绝了请求（{r.status_code}）"), 502)
    return r.content
