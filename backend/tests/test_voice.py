import httpx
import pytest

from app import voice


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v123")
    monkeypatch.delenv("ELEVENLABS_TTS_MODEL", raising=False)
    monkeypatch.setattr(voice.gemini, "MOCK", False)


def _fake(status=200, chunks=(b"ID3", b"mp3"), exc=None, seen=None):
    closed = []

    def post(url, **kw):
        if seen is not None:
            seen.update(url=url, **kw)
        if exc:
            raise exc
        return status, iter(chunks), lambda: closed.append(True)

    post.closed = closed
    return post


def test_speak_204_without_key(client, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert client.post("/api/speak", json={"text": "你好", "lang": "zh"}).status_code == 204


def test_speak_204_in_mock(client, keyed, monkeypatch):
    monkeypatch.setattr(voice.gemini, "MOCK", True)
    assert client.post("/api/speak", json={"text": "你好", "lang": "zh"}).status_code == 204


def test_speak_rejects_long_text(client):
    r = client.post("/api/speak", json={"text": "字" * 301, "lang": "zh"})
    assert r.status_code == 400 and "error" in r.json()


def test_speak_rejects_empty_text(client):
    assert client.post("/api/speak", json={"text": "  ", "lang": "zh"}).status_code == 400


def test_speak_streams_mp3(client, keyed, monkeypatch):
    seen = {}
    fake = _fake(seen=seen)
    monkeypatch.setattr(voice, "_post_stream", fake)
    r = client.post("/api/speak", json={"text": "先点图表", "lang": "zh"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3mp3"
    assert seen["url"].endswith("/text-to-speech/v123/stream")
    assert seen["headers"]["xi-api-key"] == "test-key" and seen["json"]["text"] == "先点图表"
    assert seen["json"]["model_id"] == "eleven_flash_v2_5" and fake.closed  # zh


def test_speak_rejected_is_502(client, keyed, monkeypatch):
    fake = _fake(status=401, chunks=(b'{"detail":"bad"}',))
    monkeypatch.setattr(voice, "_post_stream", fake)
    r = client.post("/api/speak", json={"text": "你好", "lang": "zh"})
    assert r.status_code == 502 and "error" in r.json() and fake.closed


def test_speak_timeout_is_504(client, keyed, monkeypatch):
    monkeypatch.setattr(voice, "_post_stream", _fake(exc=httpx.ReadTimeout("slow")))
    assert client.post("/api/speak", json={"text": "你好", "lang": "zh"}).status_code == 504


def test_speak_english_uses_same_model_as_agent(client, keyed, monkeypatch):
    seen = {}
    monkeypatch.setattr(voice, "_post_stream", _fake(seen=seen))
    client.post("/api/speak", json={"text": "Click Open.", "lang": "en"})
    assert seen["json"]["model_id"] == "eleven_flash_v2" and seen["url"].endswith("/v123/stream")


def test_default_voice_when_unset(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    assert voice.voice_id() == voice.DEFAULT_VOICE_ID
