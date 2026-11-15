"""Dock 图标用 Accessibility 的精确位置，不信 Gemini 的框。

Dock 里几十个小图标，Gemini 经常框错（让点 Chrome 却框在 Excel 上）。macOS 的 Accessibility
API 能直接给出每个 Dock 图标的标题和位置（逻辑坐标，主屏左上角为原点 —— 和高亮层一样）。

顺序：AX Dock 匹配 → OCR → 后端的框。这里任何失败都只返回 [] / None，绝不抛给调用方。
"""

from __future__ import annotations

import re
import unicodedata

Rect = tuple[int, int, int, int]  # (x, y, w, h)，逻辑点

_warned = False


def _warn_once(msg: str):
    global _warned
    if not _warned:
        _warned = True
        print(f"[ax] {msg}（之后不再提示）", flush=True)


def dock_items() -> list[tuple[str, Rect]]:
    """Dock 里每个有标题的图标：[(title, (x, y, w, h))]。没权限 / 出错 → []。"""
    try:
        import ApplicationServices as AX
        from AppKit import NSRunningApplication

        if not AX.AXIsProcessTrusted():
            _warn_once("没有辅助功能权限，Dock 图标不能精确定位（系统设置 → 隐私与安全性 → 辅助功能）")
            return []
        running = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.dock")
        if not running:
            _warn_once("找不到 Dock 进程")
            return []
        app = AX.AXUIElementCreateApplication(running[0].processIdentifier())
        AX.AXUIElementSetMessagingTimeout(app, 0.5)  # Dock 卡住也别拖住我们

        def attr(el, name):
            err, v = AX.AXUIElementCopyAttributeValue(el, name, None)
            return v if err == 0 else None

        out: list[tuple[str, Rect]] = []
        for lst in attr(app, "AXChildren") or []:
            if attr(lst, "AXRole") != "AXList":
                continue
            for it in attr(lst, "AXChildren") or []:
                if attr(it, "AXSubrole") == "AXSeparatorDockItem":
                    continue
                title, pos, size = attr(it, "AXTitle"), attr(it, "AXPosition"), attr(it, "AXSize")
                if not title or pos is None or size is None:
                    continue
                ok1, p = AX.AXValueGetValue(pos, AX.kAXValueCGPointType, None)
                ok2, s = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
                if not (ok1 and ok2) or s.width <= 0 or s.height <= 0:
                    continue
                out.append((str(title), (round(p.x), round(p.y), round(s.width), round(s.height))))
        if not out:
            _warn_once("Dock 里没读到任何图标")
        return out
    except Exception as e:  # noqa: BLE001
        _warn_once(f"读取 Dock 失败：{e}")
        return []


# 同一个 App 的不同叫法（英文 / 中文 UI 标题 / 常见简称）。比较前会统一小写、去空格和标点。
ALIASES: list[set[str]] = [
    {"google chrome", "chrome", "谷歌浏览器", "谷歌"},
    {"visual studio code", "vs code", "vscode", "code"},
    {"system settings", "system preferences", "settings", "系统设置", "系统偏好设置", "设置"},
    {"finder", "访达"},
    {"safari", "safari浏览器", "safari 浏览器"},
    {"messages", "信息"},
    {"mail", "邮件"},
    {"maps", "地图"},
    {"photos", "照片"},
    {"facetime", "facetime通话"},
    {"calendar", "日历"},
    {"contacts", "通讯录"},
    {"reminders", "提醒事项"},
    {"notes", "备忘录"},
    {"music", "音乐"},
    {"podcasts", "播客"},
    {"tv", "apple tv", "视频"},
    {"app store", "appstore", "app"},
    {"launchpad", "启动台"},
    {"trash", "bin", "废纸篓"},
    {"terminal", "终端"},
    {"preview", "预览"},
    {"calculator", "计算器"},
    {"keynote", "keynote讲演"},
    {"pages", "pages文稿"},
    {"numbers", "numbers表格"},
    {"microsoft word", "word"},
    {"microsoft excel", "excel"},
    {"microsoft powerpoint", "powerpoint"},
    {"wechat", "微信"},
    {"downloads", "下载"},
    {"iphone mirroring", "iphone镜像"},
]


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s\W_]+", "", s)


_ALIAS_NORM = [{_norm(a) for a in g} for g in ALIASES]


def _same_app(a: str, b: str) -> bool:
    return any(a in g and b in g for g in _ALIAS_NORM)


def _long_enough(s: str) -> bool:
    """子串匹配别太短：英文至少 3 个字符，中文至少 2 个字。"""
    return len(s) >= (2 if re.search(r"[㐀-鿿]", s) else 3)


def in_dock_area(box: list[int] | None) -> bool:
    """0–1000 框是否在 Dock 区域：底部 ~12%，或左右两侧 ~8%（Dock 放在侧边时）。"""
    if not box or len(box) != 4:
        return False
    cy, cx = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return cy >= 880 or cx <= 80 or cx >= 920


def mentions_dock(instruction: str | None) -> bool:
    s = (instruction or "").lower()
    return "dock" in s or "程序坞" in s


def match_dock_item(target_label: str | None, instruction: str | None, box: list[int] | None = None,
                    items: list[tuple[str, Rect]] | None = None) -> tuple[str, Rect] | None:
    """按名字在 Dock 里找 target_label。返回 (Dock 标题, rect) 或 None。

    只有后端的框在 Dock 区域、或指令提到 dock / 程序坞 时才找（免得抢了工具栏里同名的按钮）。
    精确同名 → 别名 → 子串；同一级有多个候选就不贴。items 不传就现读 Dock。
    """
    want = _norm(target_label)
    if not want or not (in_dock_area(box) or mentions_dock(instruction)):
        return None
    if items is None:
        items = dock_items()
    cands = [(t, r, _norm(t)) for t, r in items]
    tiers = [
        lambda n: n == want,
        lambda n: _same_app(n, want),
        lambda n: (_long_enough(n) and n in want) or (_long_enough(want) and want in n),
    ]
    for test in tiers:
        hits = [(t, r) for t, r, n in cands if n and test(n)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def rect_to_box(rect: Rect, screen_w: float, screen_h: float) -> list[int]:
    """(x, y, w, h) 逻辑点 → [ymin, xmin, ymax, xmax] 0–1000（coords.box_to_rect 的反过程）。"""
    x, y, w, h = rect
    return [round(y / screen_h * 1000), round(x / screen_w * 1000),
            round((y + h) / screen_h * 1000), round((x + w) / screen_w * 1000)]


def _on_screen(rect: Rect, screen_w: float, screen_h: float) -> bool:
    """Dock 自动隐藏时图标在屏幕外：那就别贴。"""
    x, y, w, h = rect
    return x >= 0 and y >= 0 and x + w <= screen_w + 1 and y + h <= screen_h + 1


def dock_box(target_label: str | None, instruction: str | None, box: list[int] | None,
             screen_wh: tuple[float, float], items: list[tuple[str, Rect]] | None = None) -> list[int] | None:
    """匹配上 Dock 图标就返回它的 0–1000 框，否则 None。永不抛异常。"""
    try:
        hit = match_dock_item(target_label, instruction, box, items)
        if hit is None or not _on_screen(hit[1], *screen_wh):
            return None
        print(f'[ax] snapped "{target_label}" to Dock item "{hit[0]}" {hit[1]}', flush=True)
        return rect_to_box(hit[1], *screen_wh)
    except Exception as e:  # noqa: BLE001
        _warn_once(f"Dock 匹配失败：{e}")
        return None


def snap_result(r: dict, screen_wh: tuple[float, float], items: list[tuple[str, Rect]] | None = None) -> dict:
    """/api/next-step 的返回：目标是 Dock 图标就把 box 换成 AX 的精确框。

    换了的话加上 snap="ax_dock"、box_gemini=原来的框；没换原样返回。永不抛异常。
    """
    try:
        if not isinstance(r, dict) or r.get("status") != "next" or not r.get("box"):
            return r
        new = dock_box(r.get("target_label"), r.get("instruction"), r["box"], screen_wh, items)
        if new is None:
            return r
        return {**r, "box": new, "box_gemini": r["box"], "snap": "ax_dock"}
    except Exception as e:  # noqa: BLE001
        _warn_once(f"Dock 吸附失败：{e}")
        return r
