"""Dock 匹配的纯逻辑：不碰真实 AX。"""

from showme import ax_snap
from showme.ax_snap import match_dock_item, snap_result
from showme.coords import box_to_rect

SCREEN = (1512, 1000)
DOCK_BOX = [960, 900, 1000, 930]  # 底部，Gemini 框错在右边
TOOLBAR_BOX = [40, 300, 70, 360]  # 屏幕顶部

ITEMS = [
    ("访达", (26, 956, 32, 44)),
    ("Safari浏览器", (90, 956, 32, 44)),
    ("Google Chrome", (700, 956, 32, 44)),
    ("系统设置", (750, 956, 32, 44)),
    ("Visual Studio Code", (800, 956, 32, 44)),
    ("Microsoft Excel", (1400, 956, 32, 44)),
    ("Numbers表格", (1440, 956, 32, 44)),
]


def title(label, instr="Click it.", box=DOCK_BOX, items=ITEMS):
    hit = match_dock_item(label, instr, box, items)
    return hit[0] if hit else None


def test_exact_and_case():
    assert title("Google Chrome") == "Google Chrome"
    assert title("google chrome") == "Google Chrome"


def test_aliases():
    assert title("Chrome") == "Google Chrome"
    assert title("VS Code") == "Visual Studio Code"
    assert title("System Settings") == "系统设置"
    assert title("Finder") == "访达"
    assert title("Safari") == "Safari浏览器"
    assert title("Excel") == "Microsoft Excel"


def test_substring():
    assert title("Numbers") == "Numbers表格"


def test_ambiguous_returns_none():
    items = ITEMS + [("Google Chrome Canary", (850, 956, 32, 44))]
    assert title("Google Chrome", items=items) == "Google Chrome"  # 精确同名优先
    assert title("Chrome", items=[("Chrome Beta", (1, 956, 3, 4)), ("Chrome Dev", (5, 956, 3, 4))]) is None
    assert title("Google", items=[("Google Chrome", (1, 956, 3, 4)), ("Google Drive", (5, 956, 3, 4))]) is None


def test_non_dock_box_not_hijacked():
    assert title("Google Chrome", box=TOOLBAR_BOX) is None
    assert title("Google Chrome", "Click Chrome in the Dock.", box=TOOLBAR_BOX) == "Google Chrome"
    assert title("访达", "点程序坞里的访达", box=None) == "访达"
    assert title("访达", "点访达", box=None) is None


def test_side_dock():
    assert title("Chrome", box=[400, 0, 440, 30]) == "Google Chrome"
    assert title("Chrome", box=[400, 970, 440, 1000]) == "Google Chrome"


def test_no_match_or_empty():
    assert title("Gmail") is None
    assert title("") is None
    assert title("Chrome", items=[]) is None


def test_snap_result_replaces_box():
    r = {"status": "next", "box": DOCK_BOX, "target_label": "Google Chrome",
         "instruction": "Click the Chrome icon in the dock."}
    out = snap_result(r, SCREEN, ITEMS)
    assert out["snap"] == "ax_dock" and out["box_gemini"] == DOCK_BOX
    x, y, w, h = box_to_rect(out["box"], *SCREEN)
    assert abs(x - 700) <= 2 and abs(y - 956) <= 2 and abs(w - 32) <= 2 and abs(h - 44) <= 2


def test_snap_result_untouched():
    r = {"status": "next", "box": TOOLBAR_BOX, "target_label": "Chrome", "instruction": "Click Chrome."}
    assert snap_result(r, SCREEN, ITEMS) is r
    done = {"status": "done", "box": None}
    assert snap_result(done, SCREEN, ITEMS) is done


def test_offscreen_dock_not_snapped():
    hidden = [("Google Chrome", (700, 1010, 32, 44))]  # 自动隐藏的 Dock
    r = {"status": "next", "box": DOCK_BOX, "target_label": "Chrome", "instruction": "x"}
    assert snap_result(r, SCREEN, hidden) is r


def test_dock_items_failure_is_empty(monkeypatch):
    import ApplicationServices as AX

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(AX, "AXIsProcessTrusted", boom)
    assert ax_snap.dock_items() == []
