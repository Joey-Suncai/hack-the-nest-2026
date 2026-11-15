import httpx

from app import voice

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 28


def post(client, data=WAV, lang="zh"):
    return client.post("/api/transcribe", files={"file": ("a.wav", data, "audio/wav")}, data={"lang": lang})


def test_mock_returns_fixed_text(client, monkeypatch):
    monkeypatch.setattr(voice.gemini, "MOCK", True)
    assert post(client).json() == {"text": "这个按钮是干嘛的？"}
    assert post(client, lang="en").json() == {"text": "What does this button do?"}


def test_no_key_is_503(client, monkeypatch):
    monkeypatch.setattr(voice.gemini, "MOCK", False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    r = post(client)
    assert r.status_code == 503 and r.json() == {"error": "语音输入没有配置，请直接打字"}


def test_empty_and_too_big_are_400(client):
    assert post(client, data=b"").status_code == 400
    assert post(client, data=b"0" * (voice.MAX_AUDIO_BYTES + 1)).status_code == 400


def _keyed(monkeypatch, response=None, exc=None, seen=None):
    monkeypatch.setattr(voice.gemini, "MOCK", False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")

    def fake(url, **kw):
        if seen is not None:
            seen.update(url=url, **kw)
        if exc:
            raise exc
        return response

    monkeypatch.setattr(voice.httpx, "post", fake)


def test_success_returns_text(client, monkeypatch):
    seen = {}
    _keyed(monkeypatch, httpx.Response(200, json={"text": " Hello there "}), seen=seen)
    r = post(client, lang="en")
    assert r.status_code == 200 and r.json() == {"text": "Hello there"}
    assert seen["url"].endswith("/speech-to-text") and seen["data"]["language_code"] == "en"
    assert seen["data"]["model_id"] and seen["headers"]["xi-api-key"] == "k"


def test_silence_returns_empty_text(client, monkeypatch):
    _keyed(monkeypatch, httpx.Response(200, json={"text": ""}))
    assert post(client).json() == {"text": ""}


def test_rejected_502_timeout_504(client, monkeypatch):
    _keyed(monkeypatch, httpx.Response(401, json={"detail": "bad"}))
    assert post(client).status_code == 502
    _keyed(monkeypatch, exc=httpx.ReadTimeout("slow"))
    assert post(client).status_code == 504
