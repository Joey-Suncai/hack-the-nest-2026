"""对冲请求：第一次请求太久没回来就再发一次，谁先成功用谁（日志：偶尔一次挂满 30 s 才 504）。"""

import threading
import time

import pytest

from app import gemini


class _FakeModels:
    def __init__(self, behaviours):
        self.behaviours, self.calls, self.lock = list(behaviours), 0, threading.Lock()

    def generate_content(self, model, contents, config):
        with self.lock:
            i = self.calls
            self.calls += 1
        kind, arg = self.behaviours[min(i, len(self.behaviours) - 1)]
        if kind == "sleep":  # 卡住：睡很久再返回
            time.sleep(arg)
            return f"slow{i}"
        if kind == "fail":
            raise RuntimeError("504 DEADLINE_EXCEEDED")
        if kind == "bad":  # 回了，但写满上限 / 半截 JSON
            if arg:
                time.sleep(arg)
            return "bad"
        return f"fast{i}"


def _client(monkeypatch, behaviours, hedge=0.05, timeout=2.0):
    models = _FakeModels(behaviours)
    monkeypatch.setattr(gemini, "_get_client", lambda: type("C", (), {"models": models})())
    monkeypatch.setattr(gemini, "HEDGE_AFTER_S", hedge)
    monkeypatch.setattr(gemini, "TIMEOUT_S", timeout)
    return models


def test_fast_first_call_sends_only_one_request(monkeypatch):
    models = _client(monkeypatch, [("ok", None)])
    assert gemini._call("p") == "fast0" and models.calls == 1


def test_stuck_first_call_is_hedged_and_second_wins(monkeypatch):
    models = _client(monkeypatch, [("sleep", 1.0), ("ok", None)])
    t0 = time.monotonic()
    assert gemini._call("p") == "fast1"
    assert models.calls == 2 and time.monotonic() - t0 < 0.5  # 没等第一个睡满 1 s


def test_fast_failure_still_retries_once(monkeypatch):
    models = _client(monkeypatch, [("fail", None), ("ok", None)])
    assert gemini._call("p") == "fast1" and models.calls == 2


def test_both_fail_gives_friendly_504(monkeypatch):
    _client(monkeypatch, [("fail", None), ("fail", None)])
    with pytest.raises(gemini.GeminiError) as e:
        gemini._call("p")
    assert e.value.status == 504


def test_hedging_off_waits_for_the_single_call(monkeypatch):
    models = _client(monkeypatch, [("sleep", 0.2), ("ok", None)], hedge=0)
    assert gemini._call("p") == "slow0" and models.calls == 1


def _parse(resp):
    if resp == "bad":
        raise gemini._BadOutput("写满输出上限 MAX_TOKENS")
    return resp


def test_unusable_reply_is_retried_immediately(monkeypatch):
    # 日志：卡在 "2525…" 写满上限 → 半截 JSON → 以前直接 502「Something went wrong」
    models = _client(monkeypatch, [("bad", None), ("ok", None)])
    assert gemini._call("p", parse=_parse) == "fast1" and models.calls == 2


def test_unusable_reply_waits_for_the_other_hedged_request(monkeypatch):
    # 第一个慢、但会成功；对冲的第二个先回来却不能用 → 不报错，接着等第一个
    models = _client(monkeypatch, [("sleep", 0.3), ("bad", None)], hedge=0.05)
    assert gemini._call("p", parse=_parse) == "slow0" and models.calls >= 2


def test_all_unusable_gives_format_error(monkeypatch):
    _client(monkeypatch, [("bad", None)])
    with pytest.raises(gemini.GeminiError) as e:
        gemini._call("p", parse=_parse)
    assert e.value.status == 502 and "格式" in e.value.message


def test_generate_json_treats_max_tokens_as_unusable(monkeypatch):
    class R:
        def __init__(self, finish, text):
            self.candidates = [type("C", (), {"finish_reason": finish})()]
            self.text, self.parsed = text, None

    from pydantic import BaseModel

    class Out(BaseModel):
        a: int

    replies = iter([R("FinishReason.MAX_TOKENS", '{"a": 2525252525'), R("FinishReason.STOP", '{"a": 1}')])
    monkeypatch.setattr(gemini, "MOCK", False)
    monkeypatch.setattr(gemini, "HEDGE_AFTER_S", 0)
    monkeypatch.setattr(gemini, "_get_client", lambda: type("C", (), {"models": type("M", (), {
        "generate_content": lambda self, model, contents, config: next(replies)})()})())
    assert gemini.generate_json("p", Out).a == 1
