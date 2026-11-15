import httpx

from app import voice


def test_not_configured_is_503(client, monkeypatch):
    monkeypatch.setattr(voice.gemini, "MOCK", False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    r = client.get("/api/agent-session")
    assert r.status_code == 503 and r.json() == {"error": "实时语音没有配置"}


def test_mock_is_503(client, monkeypatch):
    monkeypatch.setattr(voice.gemini, "MOCK", True)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_x")
    assert client.get("/api/agent-session").status_code == 503


def _keyed(monkeypatch, response=None, exc=None, seen=None):
    monkeypatch.setattr(voice.gemini, "MOCK", False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_x")

    def fake(url, **kw):
        if seen is not None:
            seen.update(url=url, **kw)
        if exc:
            raise exc
        return response

    monkeypatch.setattr(voice.httpx, "get", fake)


def test_returns_signed_url(client, monkeypatch):
    seen = {}
    _keyed(monkeypatch, httpx.Response(200, json={"signed_url": "wss://x?sig=1"}), seen=seen)
    r = client.get("/api/agent-session")
    assert r.status_code == 200 and r.json() == {"signed_url": "wss://x?sig=1", "agent_id": "agent_x"}
    assert seen["params"]["agent_id"] == "agent_x" and seen["headers"]["xi-api-key"] == "k"


def test_errors(client, monkeypatch):
    _keyed(monkeypatch, httpx.Response(401, json={"detail": "no"}))
    assert client.get("/api/agent-session").status_code == 502
    _keyed(monkeypatch, exc=httpx.ReadTimeout("slow"))
    assert client.get("/api/agent-session").status_code == 504
