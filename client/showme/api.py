"""调后端。接口以 docs/API.md 为准。所有失败都变成 ApiError（带给用户看的说明，跟界面语言一致）。"""

import httpx

from showme.config import API_BASE, API_TOKEN
from showme.i18n import get_lang, t


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status  # HTTP 状态码；连不上 / 超时为 None


_client = httpx.Client(base_url=API_BASE)


def auth_headers(path: str) -> dict:
    """/api/health 不需要口令；其他请求都带 X-ShowMe-Token（没设 SHOWME_TOKEN 就不带）。"""
    if path == "/api/health" or not API_TOKEN:
        return {}
    return {"X-ShowMe-Token": API_TOKEN}


def _request(method: str, path: str, timeout: float, **kw) -> httpx.Response:
    try:
        r = _client.request(method, path, timeout=timeout, headers=auth_headers(path), **kw)
    except httpx.TimeoutException as e:
        print(f"[api] {method} {path} → 超时（{timeout}s）：{e!r}", flush=True)
        raise ApiError(t("err_timeout"))
    except httpx.HTTPError as e:
        print(f"[api] {method} {path} → 连不上：{e!r}", flush=True)
        raise ApiError(t("err_connect"))
    _check(r, method, path)
    return r


def _check(r: httpx.Response, method: str, path: str) -> None:
    if r.status_code == 401:
        print(f"[api] {method} {path} → HTTP 401（口令不对或没设 SHOWME_TOKEN）：{r.text[:200]!r}", flush=True)
        raise ApiError(t("err_token"), 401)
    if r.status_code >= 400:
        try:
            msg = r.json()["error"]
        except Exception:  # noqa: BLE001
            print(f"[api] {method} {path} → HTTP {r.status_code}（body 不是 JSON）：{r.text[:200]!r}", flush=True)
            raise ApiError(t("err_http", code=r.status_code), r.status_code)
        # 后端的 error 是中文：英文界面下显示通用英文提示，原文打印到终端方便调试
        print(f"[api] {method} {path} → HTTP {r.status_code}: {msg}", flush=True)
        raise ApiError(msg if get_lang() == "zh" else t("err_generic"), r.status_code)


def agent_session() -> dict:
    """实时语音：{signed_url, agent_id}。503 = 后端没配实时语音。"""
    return _request("GET", "/api/agent-session", timeout=10).json()


def health() -> dict:
    return _request("GET", "/api/health", timeout=5).json()


def next_step(payload: dict) -> dict:
    return _request("POST", "/api/next-step", timeout=45, json=payload).json()


def chat(payload: dict) -> dict:
    """对话模式：{lang, message, screenshot, messages, task, frontmost_app} → {reply, action, app, goal, outline, note}"""
    return _request("POST", "/api/chat", timeout=30, json=payload).json()


def transcribe(wav: bytes, lang: str) -> str:
    """语音转文字。没配语音（503）或后端还没有这个接口（404）→ ApiError(voice_unavailable)。"""
    try:
        r = _request("POST", "/api/transcribe", timeout=30,
                     files={"file": ("speech.wav", wav, "audio/wav")}, data={"lang": lang})
    except ApiError as e:
        if e.status in (404, 503):
            raise ApiError(t("voice_unavailable"), e.status)
        raise
    return (r.json().get("text") or "").strip()


def speak(text: str, lang: str) -> bytes | None:
    """204（没配语音 / mock）→ None。"""
    r = _request("POST", "/api/speak", timeout=20, json={"text": text, "lang": lang})
    return None if r.status_code == 204 or not r.content else r.content


def speak_stream(text: str, lang: str, timeout: float = 20):
    """边收边给：一块一块 yield mp3 字节。204（没配语音 / mock）→ 什么都不 yield。
    调用方中途不要了就 close() 这个生成器（或者不再迭代）：连接会被关掉。"""
    path = "/api/speak"
    try:
        with _client.stream("POST", path, timeout=timeout, headers=auth_headers(path),
                            json={"text": text, "lang": lang}) as r:
            if r.status_code == 204:
                return
            if r.status_code >= 400:
                r.read()
                _check(r, "POST", path)
            for chunk in r.iter_bytes():
                if chunk:
                    yield chunk
    except httpx.TimeoutException as e:
        print(f"[api] POST {path} (stream) → 超时（{timeout}s）：{e!r}", flush=True)
        raise ApiError(t("err_timeout"))
    except httpx.HTTPError as e:
        print(f"[api] POST {path} (stream) → 连不上：{e!r}", flush=True)
        raise ApiError(t("err_connect"))


def guide(app: str, goal: str, lang: str) -> dict | None:
    """加分项，任何失败都返回 None，不影响主流程。"""
    try:
        return _request("POST", "/api/guide", timeout=60, json={"app": app, "goal": goal, "lang": lang}).json()
    except Exception:  # noqa: BLE001
        return None
