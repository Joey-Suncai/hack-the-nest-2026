import threading
from types import SimpleNamespace as NS

import pytest

from app import docs_guide, gemini


def test_guide_mock(client, monkeypatch):
    monkeypatch.setattr(gemini, "MOCK", True)
    r = client.post("/api/guide", json={"app": "Zoom", "goal": "share screen", "lang": "en"})
    assert r.status_code == 200
    assert r.json() == {"outline": ["[MOCK] 第一步", "[MOCK] 第二步"], "sources": []}


def test_guide_empty_goal(client):
    r = client.post("/api/guide", json={"app": "Zoom", "goal": "  "})
    assert r.status_code == 400 and r.json() == {"error": "goal 不能为空"}


def test_parse_outline_strips_and_caps():
    text = "\n".join(
        ["1. Open Zoom", "", "- Click Share", "• Pick a window", "2) Confirm", "* **Bold step**",
         "第3步：点击共享", "Step 7: Seven", "8. Eight", "9. Nine", "10. Ten"]
    )
    out = docs_guide.parse_outline(text)
    assert out == ["Open Zoom", "Click Share", "Pick a window", "Confirm", "Bold step",
                   "点击共享", "Seven", "Eight"]


def test_make_guide_real_path(client, monkeypatch):
    monkeypatch.setattr(gemini, "MOCK", False)
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return "1. 打开 Zoom\n2. 点击共享屏幕", [{"title": "zoom.us", "url": "https://x"}]

    monkeypatch.setattr(gemini, "generate_grounded", fake)
    r = client.post("/api/guide", json={"app": "Zoom", "goal": "共享屏幕", "lang": "zh"})
    assert r.json() == {"outline": ["打开 Zoom", "点击共享屏幕"],
                        "sources": [{"title": "zoom.us", "url": "https://x"}]}
    assert "Zoom" in seen["prompt"] and "简体中文" in seen["prompt"]


def test_make_guide_error_is_friendly(client, monkeypatch):
    monkeypatch.setattr(gemini, "MOCK", False)

    def boom(prompt):
        raise gemini.GeminiError("AI 服务暂时不可用（超时或网络问题），请稍后再试", 504)

    monkeypatch.setattr(gemini, "generate_grounded", boom)
    r = client.post("/api/guide", json={"app": "Zoom", "goal": "x"})
    assert r.status_code == 504 and "error" in r.json()


def test_generate_grounded_sources(monkeypatch):
    monkeypatch.setattr(gemini, "MOCK", False)
    web = lambda uri, title=None, domain=None: NS(web=NS(uri=uri, title=title, domain=domain))
    meta = NS(grounding_chunks=[
        web("https://a", "Apple Support"),
        web("https://a", "dup"),
        NS(web=None),
        web(None, "no url"),
        web("https://b", None, "zoom.us"),
    ])
    resp = NS(text=" 1. step \n", candidates=[NS(grounding_metadata=meta)])
    monkeypatch.setattr(gemini, "_call", lambda prompt, files=None, config=None: resp)
    text, sources = gemini.generate_grounded("p")
    assert text == "1. step"
    assert sources == [{"title": "Apple Support", "url": "https://a"},
                       {"title": "zoom.us", "url": "https://b"}]


def _replies(monkeypatch, replies):
    lock = threading.Lock()

    def fake(prompt, files=None, config=None):
        with lock:
            r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(gemini, "_call", fake)


def test_generate_grounded_prefers_reply_with_sources(monkeypatch):
    monkeypatch.setattr(gemini, "MOCK", False)
    no_src = NS(text="a", candidates=[NS(grounding_metadata=None)])
    with_src = NS(text="b", candidates=[NS(grounding_metadata=NS(
        grounding_chunks=[NS(web=NS(uri="https://u", title="t", domain=None))]))])
    _replies(monkeypatch, [no_src, with_src, no_src])
    assert gemini.generate_grounded("p") == ("b", [{"title": "t", "url": "https://u"}])

    _replies(monkeypatch, [no_src, gemini.GeminiError("x", 504), no_src])
    assert gemini.generate_grounded("p") == ("a", [])

    err = gemini.GeminiError("超时", 504)
    _replies(monkeypatch, [err, err, err])
    with pytest.raises(gemini.GeminiError):
        gemini.generate_grounded("p")


def test_sources_defensive():
    assert gemini._sources(NS(candidates=None)) == []
    assert gemini._sources(NS(candidates=[])) == []
    assert gemini._sources(NS(candidates=[NS(grounding_metadata=None)])) == []
    assert gemini._sources(NS(candidates=[NS(grounding_metadata=NS(grounding_chunks=None))])) == []
