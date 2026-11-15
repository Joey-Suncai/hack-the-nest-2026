"""Gemini 的唯一入口。所有 Gemini 调用都走这里（超时 + 重试 1 次 + 友好报错 + mock 模式）。

用法：
    from app import gemini
    text = gemini.generate_text("写一句口号")
    data = gemini.generate_json("提取信息...", schema=MyPydanticModel)   # -> MyPydanticModel 实例
    text = gemini.generate_text("这张图讲了什么？", files=[(image_bytes, "image/png")])
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
load_dotenv(REPO_ROOT / ".env")

DEFAULT_MODEL = "gemini-3.6-flash"
MODEL = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
MOCK = os.getenv("GEMINI_MOCK", "0").strip().lower() in ("1", "true", "yes")
TIMEOUT_S = float(os.getenv("GEMINI_TIMEOUT", "30"))
RETRIES = 1
# 对冲请求：第一次请求这么多秒还没回来就再发一次一样的，谁先回来用谁。
# 日志：大部分 2–3 s，偶尔一次挂满 30 s 才回 504 DEADLINE_EXCEEDED，重试 2.5 s 就好 → 一步卡 32–45 s。0 = 关掉
HEDGE_AFTER_S = float(os.getenv("GEMINI_HEDGE_AFTER", "6"))
# 输出上限：防止偶尔「停不下来」一直生成到超时（正常的结构化回答几百个 token）
MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "4096"))
_pool = None
GROUNDED_TRIES = 3  # generate_grounded 并发几次（见函数说明）

T = TypeVar("T", bound=BaseModel)


class GeminiError(Exception):
    """给用户看的友好错误。status 是建议返回的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise GeminiError("服务端未配置 GEMINI_API_KEY（可以先设 GEMINI_MOCK=1 用假数据）", 500)
        from google import genai
        from google.genai import types

        _client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=int(TIMEOUT_S * 1000)),
        )
    return _client


def load_prompt(name: str, **kwargs) -> str:
    """读取 backend/prompts/<name>.txt，并用 {变量} 填空。"""
    text = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    return text.format(**kwargs) if kwargs else text


def _contents(prompt: str, files: list[tuple[bytes, str]] | None):
    if not files:
        return prompt
    from google.genai import types

    parts = [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in files]
    return [*parts, prompt]


_CLIENT_ERRORS = {
    400: "Gemini 请求被拒绝（400）：API key 无效或请求参数有误",
    401: "Gemini API key 无效",
    403: "Gemini API key 没有权限",
    402: "Gemini 账户额度用完了（AI Studio 里充值 / 换 key），或先用 GEMINI_MOCK=1",
    404: f"找不到模型 {MODEL}，检查 .env 里的 GEMINI_MODEL",
}


def _executor():
    global _pool
    if _pool is None:
        from concurrent.futures import ThreadPoolExecutor

        _pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini")
    return _pool


def _call(prompt: str, files=None, config=None):
    """调 Gemini。最多发 RETRIES + 1 次：
    - 第一次很快就失败（5xx、网络）→ 等 1 s 重试
    - 第一次 HEDGE_AFTER_S 秒还没回来 → 不等它，马上再发一次，两个一起跑，谁先成功用谁（对冲请求）
    4xx（key 错、参数错）直接报错；429 记下来，别的都失败时报「太频繁」。"""
    from concurrent.futures import FIRST_COMPLETED, wait

    from google.genai import errors

    client = _get_client()

    def once():
        return client.models.generate_content(model=MODEL, contents=_contents(prompt, files), config=config)

    pool = _executor()
    running = [pool.submit(once)]
    sent, last = 1, None
    deadline = time.monotonic() + TIMEOUT_S + max(HEDGE_AFTER_S, 1.0) + 2
    while running:
        can_hedge = sent <= RETRIES and HEDGE_AFTER_S > 0
        timeout = HEDGE_AFTER_S if can_hedge else max(0.1, deadline - time.monotonic())
        done, _ = wait(running, timeout=timeout, return_when=FIRST_COMPLETED)
        if not done:
            if can_hedge:
                print(f"[gemini] 第 {sent} 次请求 {HEDGE_AFTER_S:.0f} s 还没回来 → 再发一次，谁先回来用谁", flush=True)
                running.append(pool.submit(once))
                sent += 1
                continue
            break  # 都超时了
        for f in done:
            running.remove(f)
            try:
                resp = f.result()
                if sent > 1:
                    print(f"[gemini] 第 {sent} 次请求里有一个成功了", flush=True)
                return resp
            except errors.ClientError as e:  # 4xx：key 错、参数错、配额用完，重试没用（429 除外）
                print(f"[gemini] client error {e.code}: {e}")
                if e.code != 429:
                    raise GeminiError(_CLIENT_ERRORS.get(e.code, f"Gemini 请求被拒绝（{e.code}）"), 502)
                last = GeminiError("Gemini 调用太频繁 / 配额用完了，请稍后再试", 429)
            except Exception as e:  # 5xx、超时、网络问题
                print(f"[gemini] attempt failed: {type(e).__name__}: {e}")
                last = e
        if not running and sent <= RETRIES:  # 失败得很快、还没对冲过：照旧重试一次
            time.sleep(1)
            running.append(pool.submit(once))
            sent += 1
    if isinstance(last, GeminiError):
        raise last
    raise GeminiError("AI 服务暂时不可用（超时或网络问题），请稍后再试", 504)


def generate_text(prompt: str, files: list[tuple[bytes, str]] | None = None) -> str:
    """普通文本输出。files = [(bytes, mime_type)]，支持 image/png、image/jpeg、application/pdf 等。"""
    if MOCK:
        extra = f"（附带 {len(files)} 个文件）" if files else ""
        return f"[MOCK] 这是假回复{extra}。你的输入是：{prompt[:200]}"
    resp = _call(prompt, files)
    text = (resp.text or "").strip()
    if not text:
        raise GeminiError("AI 没有返回内容（可能被安全过滤），换个说法试试", 502)
    return text


def generate_json(
    prompt: str,
    schema: type[T],
    files: list[tuple[bytes, str]] | None = None,
    thinking: str | None = None,
) -> T:
    """结构化输出：返回 schema（pydantic 模型）的实例。

    thinking：思考量（"minimal" / "low" / "medium" / "high"，只有 Gemini 3 系列支持）；None = 模型默认。
    """
    if MOCK:
        return _mock_instance(schema)
    from google.genai import types

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level=thinking.upper()) if thinking else None,
        max_output_tokens=MAX_OUTPUT_TOKENS or None,
    )
    resp = _call(prompt, files, config)
    if isinstance(resp.parsed, schema):
        return resp.parsed
    try:
        return schema.model_validate(json.loads(resp.text or ""))
    except Exception:
        raise GeminiError("AI 返回的格式不对，请重试", 502)


def _mock_instance(schema: type[T]) -> T:
    """按字段类型造一个假的实例，前端联调用。"""
    fake = {str: "mock", int: 1, float: 1.0, bool: True, list: [], dict: {}}
    data = {}
    for name, field in schema.model_fields.items():
        ann = field.annotation
        origin = getattr(ann, "__origin__", ann)
        data[name] = fake.get(origin, None)
    return schema.model_construct(**data)


def generate_grounded(prompt: str) -> tuple[str, list[dict]]:
    """开 Google 搜索工具生成文本。返回 (文本, [{"title", "url"}] 来源)。

    搜不搜由模型自己决定，实测单次只有约一半会真的搜（有来源）。所以并发发
    GROUNDED_TRIES 次，谁先带着来源回来就用谁；都没来源就用第一个有文本的结果。
    thinking 调低：单次约 2–5 秒，并发所以总耗时 ≈ 单次。
    """
    if MOCK:
        return f"[MOCK] 这是假回复。你的输入是：{prompt[:200]}", []
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from google.genai import types

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        thinking_config=types.ThinkingConfig(thinking_level="low"),
    )
    fallback, error = None, None
    pool = ThreadPoolExecutor(GROUNDED_TRIES)
    try:
        futures = [pool.submit(_call, prompt, None, config) for _ in range(GROUNDED_TRIES)]
        for fut in as_completed(futures):
            try:
                resp = fut.result()
            except GeminiError as e:
                error = error or e
                continue
            text = (resp.text or "").strip()
            if not text:
                continue
            sources = _sources(resp)
            if sources:
                return text, sources
            fallback = fallback or (text, [])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if fallback:
        print("[gemini] grounded calls returned no sources")
        return fallback
    raise error or GeminiError("AI 没有返回内容（可能被安全过滤），换个说法试试", 502)


def _sources(resp) -> list[dict]:
    """从 grounding_metadata 里取网页来源，按 url 去重。任何字段都可能是 None。"""
    try:
        meta = resp.candidates[0].grounding_metadata
        chunks = (meta.grounding_chunks if meta else None) or []
    except (AttributeError, IndexError, TypeError):
        return []
    out, seen = [], set()
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        url = getattr(web, "uri", None) if web else None
        if not url or url in seen:
            continue
        seen.add(url)
        title = getattr(web, "title", None) or getattr(web, "domain", None) or url
        out.append({"title": title, "url": url})
    return out
