import math

from showme import ax_tree
from showme.ax_tree import chosen_line, finalize, match_app, pick_name, rect_ok, to_box

W, H = 1470.0, 956.0


def test_to_box_converts_and_clamps():
    assert to_box((147, 95.6, 147, 95.6), W, H) == [100, 100, 200, 200]
    assert to_box((0, 0, W, H), W, H) == [0, 0, 1000, 1000]
    assert to_box((-20, -10, 40, 20), W, H) == [0, 0, 10, 14]  # 超出屏幕的部分夹到 0
    assert to_box((W - 10, H - 10, 100, 100), W, H)[2:] == [1000, 1000]
    assert all(isinstance(v, int) for v in to_box((1.4, 2.6, 3.3, 4.4), W, H))


def test_rect_ok_filters_bad_frames():
    assert rect_ok((10, 10, 50, 20), W, H)
    assert not rect_ok((10, 10, 0, 20), W, H)  # 零宽
    assert not rect_ok((10, 10, 50, -1), W, H)
    assert not rect_ok((math.inf, 10, 50, 20), W, H)
    assert not rect_ok((10, 10, float("-inf"), 20), W, H)
    assert not rect_ok((10, math.nan, 50, 20), W, H)
    assert not rect_ok((37, 956, 35, 47), W, H)  # 自动隐藏的 Dock 图标在屏幕下面
    assert not rect_ok((2000, 10, 50, 20), W, H)  # 副屏
    assert not rect_ok(None, W, H)
    assert not rect_ok((10, 900, 50, 20), W, H, clip=(0, 0, 800, 600))  # 滚出窗口的行
    assert rect_ok((10, 100, 50, 20), W, H, clip=(0, 0, 800, 600))


def test_finalize_sorts_dedupes_and_caps_named_first():
    raw = [("AXButton", "B", [100, 500, 110, 510]), ("AXButton", "A", [100, 10, 110, 20]),
           ("AXButton", "A", [100, 10, 110, 20]), ("AXLink", None, [5, 5, 10, 10])]
    out = finalize(raw)
    assert [e["name"] for e in out] == [None, "A", "B"]
    assert [e["id"] for e in out] == [1, 2, 3]
    assert set(out[0]) == {"id", "role", "name", "box"}

    many = [("AXCell", None, [i % 1000, 0, i % 1000 + 1, 1]) for i in range(300)]
    many += [("AXButton", f"n{i}", [999, i, 1000, i + 1]) for i in range(10)]  # 有名字的在最下面
    out = finalize(many)
    assert len(out) == 250
    assert sum(1 for e in out if e["name"]) == 10  # 有名字的都留下了
    assert [e["id"] for e in out] == list(range(1, 251))
    keys = [(e["box"][0], e["box"][1]) for e in out]
    assert keys == sorted(keys)


def test_pick_name_fallbacks():
    assert pick_name({"AXTitle": "New Tab", "AXDescription": "x"}.get) == "New Tab"
    assert pick_name({"AXTitle": "  ", "AXDescription": "Back"}.get) == "Back"
    assert pick_name({"AXValue": 1, "AXHelp": "Toggle"}.get) == "Toggle"  # AXValue 不是字符串 → 跳过
    assert pick_name({"AXValue": "hello\n world"}.get) == "hello world"
    assert pick_name({"AXPlaceholderValue": "Search"}.get) == "Search"
    assert pick_name({"AXTitleUIElement.value": "Name:"}.get) == "Name:"
    assert pick_name({}.get) is None
    assert len(pick_name({"AXTitle": "x" * 200}.get)) == 60

    def boom(a):
        raise RuntimeError
    assert pick_name(boom) is None


def test_match_app_names():
    running = [(1, ["Code", "Visual Studio Code", "Electron"]), (2, ["zoom.us", "zoom.us", "xos"]),
               (3, ["Google Chrome", "Google Chrome", "Chrome"]), (4, ["访达", "Finder", "finder"])]
    assert match_app("VS Code", running) == 1
    assert match_app("visual studio code", running) == 1
    assert match_app("Zoom", running) == 2
    assert match_app("chrome", running) == 3
    assert match_app("Finder", running) == 4
    assert match_app("Numbers", running) is None
    assert match_app(None, running) is None


def test_collect_returns_empty_when_ax_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("AX exploded")
    monkeypatch.setattr(ax_tree, "_collect", boom)
    assert ax_tree.collect("Code") == []


def test_chosen_line():
    els = finalize([("AXButton", "New Tab", [1, 2, 3, 4])])
    assert chosen_line({"status": "next", "element_id": 1}, els) == "[ax] Gemini chose #1 AXButton 'New Tab'"
    assert chosen_line({"status": "next", "element_id": None}, els) == "[ax] Gemini used its own box"
    assert chosen_line({"status": "next", "element_id": 99}, els) == "[ax] Gemini used its own box"
    assert chosen_line({"status": "next"}, []) is None
