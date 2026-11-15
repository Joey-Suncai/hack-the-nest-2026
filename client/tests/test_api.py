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


def test_error_body_is_passed_through(monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(400, json={"error": "goal 不能为空"}))
    with pytest.raises(api.ApiError, match="goal 不能为空"):
        api.next_step({})


def test_non_json_error(monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(api.ApiError, match="HTTP 500"):
        api.next_step({})


def test_speak_204_returns_none(monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(204))
    assert api.speak("你好", "zh") is None


def test_guide_failure_returns_none(monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(404, json={"detail": "Not Found"}))
    assert api.guide("Numbers", "做图", "zh") is None
