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


# ---- 用假的 AX 测 _Walker（Chrome / VS Code 实测发现的两个问题）----
class _Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Sz:
    def __init__(self, w, h):
        self.width, self.height = w, h


class _FakeAX:
    kAXValueAXErrorType, kAXValueCGPointType, kAXValueCGSizeType = 99, 1, 2

    def AXUIElementCopyMultipleAttributeValues(self, el, names, opt, _):
        return 0, [el.get(n) for n in names]

    def AXValueGetType(self, v):
        return 0

    def AXValueGetValue(self, v, t, _):
        return True, v

    def AXUIElementCopyAttributeValue(self, el, name, _):
        return (0, el[name]) if name in el else (-25205, None)


def _el(role, x, y, w, h, title=None, kids=(), **extra):
    return {"AXRole": role, "AXPosition": _Pt(x, y), "AXSize": _Sz(w, h), "AXTitle": title,
            "AXChildren": list(kids), **extra}


def _walk_window(win):
    wk = ax_tree._Walker(_FakeAX(), (W, H), deadline=float("inf"))
    wk.window(win)
    return [(r, n) for r, n, _ in wk.out]


def test_walker_descends_into_groups_reported_as_not_expanded():
    # Chromium 把普通容器、按钮也报 AXExpanded=False；只有可折叠控件才停
    btn = _el("AXButton", 10, 200, 40, 30, "Source Control", AXExpanded=False)
    grp = _el("AXGroup", 0, 160, 300, 400, kids=[btn], AXExpanded=False)
    hidden_row = _el("AXRow", 10, 300, 200, 20, "child row")
    tri = _el("AXDisclosureTriangle", 10, 260, 16, 16, "folder", kids=[hidden_row], AXExpanded=False)
    win = _el("AXWindow", 0, 155, W, 801, kids=[grp, tri])
    got = _walk_window(win)
    assert ("AXButton", "Source Control") in got
    assert ("AXDisclosureTriangle", "folder") in got
    assert ("AXRow", "child row") not in got  # 折叠起来的三角形里的东西不读


def test_walker_keeps_controls_outside_reported_window_frame_but_clips_rows():
    # Chrome 报的窗口框从 y=155 开始，标签栏的「新标签页」在 y=34
    new_tab = _el("AXButton", 602, 34, 28, 41, "新标签页")
    row_above = _el("AXRow", 10, 100, 200, 20, "scrolled-out row")
    row_in = _el("AXRow", 10, 400, 200, 20, "visible row")
    win = _el("AXWindow", 0, 155, W, 801, kids=[new_tab, row_above, row_in])
    got = _walk_window(win)
    assert ("AXButton", "新标签页") in got
    assert ("AXRow", "visible row") in got
    assert ("AXRow", "scrolled-out row") not in got
