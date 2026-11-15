import httpx
import pytest

from app import voice


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v123")
    monkeypatch.setattr(voice.gemini, "MOCK", False)


def _fake_post(response=None, exc=None, seen=None):
    def post(url, **kw):
        if seen is not None:
            seen.update(url=url, **kw)
        if exc:
            raise exc
        return response
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


def test_speak_returns_mp3(client, keyed, monkeypatch):
    seen = {}
    monkeypatch.setattr(voice.httpx, "post", _fake_post(httpx.Response(200, content=b"ID3mp3"), seen=seen))
    r = client.post("/api/speak", json={"text": "先点图表", "lang": "zh"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3mp3"
    assert seen["url"].endswith("/text-to-speech/v123")
    assert seen["headers"]["xi-api-key"] == "test-key" and seen["json"]["text"] == "先点图表"


def test_speak_rejected_is_502(client, keyed, monkeypatch):
    monkeypatch.setattr(voice.httpx, "post", _fake_post(httpx.Response(401, json={"detail": "bad"})))
    r = client.post("/api/speak", json={"text": "你好", "lang": "zh"})
    assert r.status_code == 502 and "error" in r.json()


def test_speak_timeout_is_504(client, keyed, monkeypatch):
    monkeypatch.setattr(voice.httpx, "post", _fake_post(exc=httpx.ReadTimeout("slow")))
    assert client.post("/api/speak", json={"text": "你好", "lang": "zh"}).status_code == 504
