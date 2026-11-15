"""调后端。接口以 docs/API.md 为准。所有失败都变成 ApiError（带给用户看的中文说明）。"""

import httpx

from showme.config import API_BASE
from showme.i18n import t


class ApiError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


_client = httpx.Client(base_url=API_BASE)


def _request(method: str, path: str, timeout: float, **kw) -> httpx.Response:
    try:
        r = _client.request(method, path, timeout=timeout, **kw)
    except httpx.TimeoutException:
        raise ApiError(t("err_timeout"))
    except httpx.HTTPError:
        raise ApiError(t("err_connect"))
    if r.status_code >= 400:
        try:
            msg = r.json()["error"]
        except Exception:  # noqa: BLE001
            msg = t("err_http", code=r.status_code)
        raise ApiError(msg)
    return r


def health() -> dict:
    return _request("GET", "/api/health", timeout=5).json()


def next_step(payload: dict) -> dict:
    return _request("POST", "/api/next-step", timeout=45, json=payload).json()


def speak(text: str, lang: str) -> bytes | None:
    """204（没配语音 / mock）→ None。"""
    r = _request("POST", "/api/speak", timeout=20, json={"text": text, "lang": lang})
    return None if r.status_code == 204 or not r.content else r.content


def guide(app: str, goal: str, lang: str) -> dict | None:
    """加分项，任何失败都返回 None，不影响主流程。"""
    try:
        return _request("POST", "/api/guide", timeout=60, json={"app": app, "goal": goal, "lang": lang}).json()
    except Exception:  # noqa: BLE001
        return None
