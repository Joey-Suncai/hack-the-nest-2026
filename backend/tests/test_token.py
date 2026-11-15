from conftest import TINY_PNG_B64

BODY = {"app": "a", "goal": " ", "screenshot": TINY_PNG_B64}


def test_no_token_configured_is_open(client, monkeypatch):
    monkeypatch.delenv("SHOWME_TOKEN", raising=False)
    assert client.post("/api/next-step", json=BODY).status_code == 400  # 走到正常校验（goal 为空）


def test_wrong_or_missing_token_is_401(client, monkeypatch):
    monkeypatch.setenv("SHOWME_TOKEN", "s3cret")
    r = client.post("/api/next-step", json=BODY)
    assert r.status_code == 401 and "error" in r.json()
    assert client.post("/api/next-step", json=BODY, headers={"X-ShowMe-Token": "nope"}).status_code == 401
    assert client.get("/api/agent-session").status_code == 401


def test_right_token_passes(client, monkeypatch):
    monkeypatch.setenv("SHOWME_TOKEN", "s3cret")
    r = client.post("/api/next-step", json=BODY, headers={"X-ShowMe-Token": "s3cret"})
    assert r.status_code == 400 and r.json() == {"error": "goal 不能为空"}


def test_health_is_always_open(client, monkeypatch):
    monkeypatch.setenv("SHOWME_TOKEN", "s3cret")
    assert client.get("/api/health").status_code == 200


def test_transcribe_multipart_needs_token(client, monkeypatch):
    monkeypatch.setenv("SHOWME_TOKEN", "s3cret")
    r = client.post("/api/transcribe", files={"file": ("a.wav", b"", "audio/wav")})
    assert r.status_code == 401
