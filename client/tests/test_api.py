import httpx
import pytest

from showme import api


def _use(monkeypatch, handler):
    monkeypatch.setattr(api, "_client", httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x"))


def test_connection_error_is_friendly(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("refused")

    _use(monkeypatch, boom)
    with pytest.raises(api.ApiError, match="reach the server"):
        api.next_step({})


def test_backend_error_is_generic_in_english(monkeypatch, capsys):
    from showme import i18n

    i18n.set_lang("en")
    _use(monkeypatch, lambda r: httpx.Response(400, json={"error": "goal 不能为空"}))
    with pytest.raises(api.ApiError, match="Something went wrong"):
        api.next_step({})
    assert "goal 不能为空" in capsys.readouterr().out  # 原文打到终端


def test_backend_error_passed_through_in_chinese(monkeypatch):
    from showme import i18n

    i18n.set_lang("zh")
    _use(monkeypatch, lambda r: httpx.Response(400, json={"error": "goal 不能为空"}))
    try:
        with pytest.raises(api.ApiError, match="goal 不能为空"):
            api.next_step({})
    finally:
        i18n.set_lang("en")


def test_non_json_error(monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(api.ApiError, match="HTTP 500"):
        api.next_step({})


def test_speak_204_returns_none(monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(204))
    assert api.speak("你好", "zh") is None


def test_guide_failure_returns_none(monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(404, json={"detail": "Not Found"}))
    assert api.guide("VS Code", "Clone a repo", "en") is None


def test_token_header_sent_except_health(monkeypatch):
    seen = {}

    def handler(r):
        seen[r.url.path] = r.headers.get("X-ShowMe-Token")
        return httpx.Response(200, json={"status": "ok", "reply": "", "action": "clarify"})

    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    _use(monkeypatch, handler)
    api.health()
    api.chat({"message": "hi"})
    assert seen == {"/api/health": None, "/api/chat": "test-token"}


def test_no_token_no_header(monkeypatch):
    seen = {}
    monkeypatch.setattr(api, "API_TOKEN", None)
    _use(monkeypatch, lambda r: (seen.update(h=r.headers.get("X-ShowMe-Token")), httpx.Response(200, json={}))[1])
    api.chat({"message": "hi"})
    assert seen == {"h": None}


def test_401_shows_token_message(monkeypatch):
    from showme import i18n

    i18n.set_lang("en")
    monkeypatch.setattr(api, "API_TOKEN", "wrong")
    _use(monkeypatch, lambda r: httpx.Response(401, json={"error": "口令不对"}))
    with pytest.raises(api.ApiError, match="Wrong access token — check SHOWME_TOKEN"):
        api.next_step({})
