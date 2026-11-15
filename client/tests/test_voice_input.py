import io
import json
import wave

import httpx
import numpy as np
import pytest

from showme import api, i18n
from showme.recorder import to_wav


def test_to_wav_is_16k_mono():
    data = to_wav(np.zeros(16000, np.int16))
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (16000, 1, 2, 16000)


def _use(monkeypatch, handler):
    monkeypatch.setattr(api, "_client", httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x"))


@pytest.mark.parametrize("status", [404, 503])
def test_transcribe_unavailable_asks_to_type(monkeypatch, status):
    i18n.set_lang("en")
    _use(monkeypatch, lambda r: httpx.Response(status, json={"error": "语音输入没有配置，请直接打字"}))
    with pytest.raises(api.ApiError, match="please type instead"):
        api.transcribe(b"RIFF", "en")


def test_transcribe_sends_wav_and_lang(monkeypatch):
    seen = {}

    def handler(r):
        body = r.read()
        seen["wav"] = b'filename="speech.wav"' in body and b"audio/wav" in body
        seen["lang"] = b'name="lang"' in body and b"en" in body
        return httpx.Response(200, json={"text": "  What does this button do?  "})

    _use(monkeypatch, handler)
    assert api.transcribe(b"RIFF....", "en") == "What does this button do?"
    assert seen == {"wav": True, "lang": True}


def test_chat_posts_payload(monkeypatch):
    seen = {}

    def handler(r):
        seen.update(json.loads(r.read()))
        return httpx.Response(200, json={"reply": "ok", "action": "clarify", "app": None, "goal": None, "outline": None, "note": None})

    _use(monkeypatch, handler)
    out = api.chat({"lang": "en", "message": "hi", "screenshot": "x", "messages": [], "task": None, "frontmost_app": "Finder"})
    assert out["action"] == "clarify" and seen["frontmost_app"] == "Finder" and seen["task"] is None
