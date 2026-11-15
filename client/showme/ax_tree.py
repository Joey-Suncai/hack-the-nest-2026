"""读目标 App 真实的 UI 控件（Accessibility），连同精确框一起发给 /api/next-step 的 `elements`。

Gemini 从里面按 id 选一个，后端用这个元素的精确框代替 Gemini 自己估的框（框经常偏一点）。
接口见 docs/API.md。这里任何失败都只返回 []（请求里就不带 elements），绝不抛给调用方。

坐标：AX 给的是逻辑点，原点在主屏左上角（和高亮层一样）→ [ymin, xmin, ymax, xmax] 0–1000。
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Callable

from showme import ax_snap

ROLES = frozenset({
    "AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXCheckBox", "AXRadioButton", "AXPopUpButton",
    "AXMenuButton", "AXMenuItem", "AXMenuBarItem", "AXLink", "AXTab", "AXComboBox", "AXSlider",
    "AXDisclosureTriangle", "AXCell", "AXRow", "AXSwitch", "AXSegmentedControl", "AXDockItem",
})
MAX_ELEMENTS = 250
MAX_DEPTH = 40
MAX_NODES = 4000
BUDGET_S = 0.15
NAME_MAX = 60
TIMEOUT_S = 0.3  # 每次 AX 调用最多等多久（默认 6 秒，App 卡住会拖住我们）
SKIP_APPS = {"showme", "python", "terminal", "claude"}  # 「最前面的 App」里跳过这些
COLLAPSIBLE_ROLES = {"AXDisclosureTriangle", "AXOutline"}  # 折叠后子元素真的不在屏幕上的控件
CLIP_ROLES = {"AXRow", "AXCell"}  # 只有这些会滚出窗口，要按窗口框裁
# AXStaticText 只在这些容器里、或者在不是主窗口的小窗口（弹出列表 / 下拉框）里才收：否则每个标签都进来，列表会被刷满
TEXT_PARENT_ROLES = {"AXList", "AXTable", "AXOutline", "AXMenu"}
NAME_ATTRS = ("AXTitle", "AXDescription", "AXValue", "AXHelp", "AXPlaceholderValue")
# 每个节点一次 IPC 取完
_ATTRS = ["AXRole", "AXSubrole", "AXPosition", "AXSize", "AXChildren", "AXHidden", "AXExpanded", "AXSelected",
          *NAME_ATTRS, "AXTitleUIElement"]

Rect = tuple[float, float, float, float]  # (x, y, w, h)，逻辑点

_manual_done: set[int] = set()
_timeout_set = False
_last_stats = ""


# ================= 纯函数（不碰 AX，可测试）=================
def rect_ok(rect, screen_w: float, screen_h: float, clip: Rect | None = None) -> bool:
    """框能用：四个数都是有限的、宽高 > 0、中心在屏幕上（有 clip 时也要在 clip 里，比如窗口外滚出去的行）。"""
    try:
        x, y, w, h = (float(v) for v in rect)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return False
    cx, cy = x + w / 2, y + h / 2
    if not (0 <= cx <= screen_w and 0 <= cy <= screen_h):
        return False
    if clip is not None:
        X, Y, W, H = clip
        if not (X <= cx <= X + W and Y <= cy <= Y + H):
            return False
    return True


def to_box(rect, screen_w: float, screen_h: float) -> list[int]:
    """(x, y, w, h) 逻辑点 → [ymin, xmin, ymax, xmax] 0–1000，夹在 0–1000。"""
    x, y, w, h = (float(v) for v in rect)

    def n(v: float, full: float) -> int:
        return max(0, min(1000, round(v / full * 1000)))

    return [n(y, screen_h), n(x, screen_w), n(y + h, screen_h), n(x + w, screen_w)]


def pick_name(get: Callable[[str], Any]) -> str | None:
    """第一个非空的 AXTitle / AXDescription / AXValue（只要字符串）/ AXHelp / AXPlaceholderValue /
    AXTitleUIElement 的值。截到 60 个字符；都没有 → None。get(attr) 返回属性值或 None。"""
    for a in (*NAME_ATTRS, "AXTitleUIElement.value"):
        try:
            v = get(a)
        except Exception:  # noqa: BLE001
            v = None
        if isinstance(v, str):
            s = " ".join(v.split())
            if s:
                return s[:NAME_MAX]
    return None


def finalize(raw: list[tuple[str, str | None, list[int]]]) -> list[dict]:
    """[(role, name, box)] → 去重、从上到下再从左到右排序、最多 250 个（超了先留有名字的），id 从 1 开始。"""
    seen, items = set(), []
    for role, name, box in raw:
        k = (role, name, tuple(box))
        if k not in seen:
            seen.add(k)
            items.append((role, name, box))
    if len(items) > MAX_ELEMENTS:
        items.sort(key=lambda e: (e[1] is None, e[2][0], e[2][1]))
        items = items[:MAX_ELEMENTS]
    items.sort(key=lambda e: (e[2][0], e[2][1]))
    return [{"id": i, "role": r, "name": n, "box": b} for i, (r, n, b) in enumerate(items, 1)]


def match_app(want: str | None, apps: list[tuple[int, list[str]]]) -> int | None:
    """按名字找进程：apps = [(pid, [localizedName, bundle 名, ...])]。不分大小写；
    精确 → 别名（"VS Code" = "Code"）→ 子串（"Zoom" ⊂ "zoom.us"）。找不到 → None。"""
    w = ax_snap._norm(want)
    if not w:
        return None
    cands = [(pid, [ax_snap._norm(n) for n in names if n]) for pid, names in apps]
    tiers = [
        lambda n: n == w,
        lambda n: ax_snap._same_app(n, w),
        lambda n: (ax_snap._long_enough(n) and n in w) or (ax_snap._long_enough(w) and w in n),
    ]
    for test in tiers:
        for pid, names in cands:
            if any(n and test(n) for n in names):
                return pid
    return None


def stats_line(app: str, elements: list[dict], ms: float) -> str:
    named = sum(1 for e in elements if e.get("name"))
    return f"[ax] {app}: {len(elements)} elements ({named} named) in {ms:.0f} ms"


def chosen_line(r: dict, elements: list[dict] | None) -> str | None:
    """后端返回之后的一行日志。没发 elements → None。"""
    if not elements or not isinstance(r, dict) or r.get("status") != "next":
        return None
    eid = r.get("element_id")
    el = next((e for e in elements if e["id"] == eid), None) if isinstance(eid, int) else None
    if el is None:
        return "[ax] Gemini used its own box"
    return f"[ax] Gemini chose #{eid} {el['role']} {el['name']!r}"


# ================= AX =================
def _screen_wh() -> tuple[float, float]:
    import Quartz as Q

    b = Q.CGDisplayBounds(Q.CGMainDisplayID())
    return float(b.size.width), float(b.size.height)


def _running() -> list[tuple[int, list[str]]]:
    from AppKit import NSApplicationActivationPolicyRegular, NSWorkspace

    out = []
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.activationPolicy() != NSApplicationActivationPolicyRegular:
            continue
        names = [str(a.localizedName() or "")]
        url = a.bundleURL()
        if url is not None and url.lastPathComponent():
            names.append(str(url.lastPathComponent()).removesuffix(".app"))
        bid = str(a.bundleIdentifier() or "")
        if bid:
            names.append(bid.rsplit(".", 1)[-1])
        out.append((int(a.processIdentifier()), names))
    return out


def _frontmost_pid(running: list[tuple[int, list[str]]]) -> int | None:
    """窗口从前到后，第一个不是 ShowMe / 终端 / Claude 的普通 App。"""
    import Quartz as Q

    regular = {pid: names for pid, names in running}
    me = os.getpid()
    infos = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
                                         Q.kCGNullWindowID) or []
    for w in infos:
        pid = int(w.get("kCGWindowOwnerPID", -1))
        if w.get("kCGWindowLayer", 1) != 0 or pid == me or pid not in regular:
            continue
        if any(ax_snap._norm(n) in SKIP_APPS for n in regular[pid] if n):
            continue
        return pid
    return None


def _target(app_name: str | None) -> tuple[int, str] | None:
    running = _running()
    pid = match_app(app_name, running)
    if pid is None or pid == os.getpid():
        pid = _frontmost_pid(running)
    if pid is None:
        return None
    name = next((names[0] for p, names in running if p == pid), str(pid))
    return pid, name


class _Walker:
    def __init__(self, AX, screen_wh, deadline):
        self.AX, self.sw, self.sh, self.deadline = AX, screen_wh[0], screen_wh[1], deadline
        self.nodes = 0
        self.out: list[tuple[str, str | None, list[int]]] = []

    def over(self) -> bool:
        return self.nodes >= MAX_NODES or time.perf_counter() > self.deadline

    def attrs(self, el, names) -> dict:
        AX = self.AX
        err, vals = AX.AXUIElementCopyMultipleAttributeValues(el, names, 0, None)
        if err != 0 or vals is None:
            return {}
        d = {}
        for n, v in zip(names, vals):
            if v is None:
                continue
            if type(v).__name__.startswith("AXValue") and AX.AXValueGetType(v) == AX.kAXValueAXErrorType:
                continue
            d[n] = v
        return d

    def attr(self, el, name):
        err, v = self.AX.AXUIElementCopyAttributeValue(el, name, None)
        return v if err == 0 else None

    def rect(self, d: dict) -> Rect | None:
        AX = self.AX
        pos, size = d.get("AXPosition"), d.get("AXSize")
        if pos is None or size is None:
            return None
        ok1, p = AX.AXValueGetValue(pos, AX.kAXValueCGPointType, None)
        ok2, s = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
        if not (ok1 and ok2):
            return None
        return (p.x, p.y, s.width, s.height)

    def name(self, d: dict) -> str | None:
        def get(a):
            if a == "AXTitleUIElement.value":
                t = d.get("AXTitleUIElement")
                return self.attr(t, "AXValue") or self.attr(t, "AXTitle") if t is not None else None
            return d.get(a)
        return pick_name(get)

    def add(self, role: str, d: dict, clip: Rect | None):
        r = self.rect(d)
        # 窗口框只用来去掉列表里滚出去的行 / 单元格。Chrome 报的窗口框不含标签栏（y=155 起，
        # 「新标签页」在 y=34），对所有控件都裁掉会把整条标签栏和工具栏丢掉
        if role not in CLIP_ROLES:
            clip = None
        if r is not None and rect_ok(r, self.sw, self.sh, clip):
            self.out.append((role, self.name(d), to_box(r, self.sw, self.sh)))

    def walk(self, el, depth: int = 0, clip: Rect | None = None, children: bool = True, text: bool = False):
        """text：这一层的 AXStaticText 也收（在列表 / 表格 / 菜单里，或者在弹出的小窗口里）。"""
        if depth > MAX_DEPTH or self.over():
            return
        self.nodes += 1
        d = self.attrs(el, _ATTRS)
        if not d or d.get("AXHidden") is True:
            return
        role = str(d.get("AXRole") or "")
        if role in ROLES or (text and role == "AXStaticText"):
            self.add(role, d, clip)
        text = text or role in TEXT_PARENT_ROLES
        # Chromium / Electron（Chrome、VS Code）把普通按钮、容器也报成 AXExpanded=False，
        # 只有真正能折叠的控件才认这个标记，否则整个窗口都读不进来（实测 VS Code 53 个只剩 0 个）
        if not children or (d.get("AXExpanded") is False and role in COLLAPSIBLE_ROLES):
            return
        if role == "AXMenuBarItem" and d.get("AXSelected") is not True:
            return  # 没打开的菜单：里面的菜单项不在屏幕上
        for c in d.get("AXChildren") or []:
            self.walk(c, depth + 1, clip, text=text)

    def window(self, w, main: bool = True):
        """main=False：弹出列表 / 下拉框 / 对话框这类别的窗口，里面的 AXStaticText 也收。"""
        d = self.attrs(w, ["AXPosition", "AXSize", "AXMinimized"])
        if d.get("AXMinimized") is True:
            return
        r = self.rect(d)
        if r is None or not rect_ok(r, self.sw, self.sh):
            return
        self.walk(w, 0, clip=r, text=not main)

    def other_windows(self, windows: list, focused) -> tuple[list, list]:
        """除了聚焦窗口之外、屏幕上的其它窗口（任何 subrole：弹出列表、下拉框、对话框……），按面积从小到大。
        返回 (比聚焦窗口小的, 不比它小的)：小的在聚焦窗口之前读，大的（比如 Chrome 另一个窗口）最后有时间再读。"""
        fa = None
        if focused is not None:
            fr = self.rect(self.attrs(focused, ["AXPosition", "AXSize"]))
            fa = fr[2] * fr[3] if fr else None
        seen, sized = [], []
        for w in windows:
            if self.over():
                break
            if w is None or (focused is not None and w == focused) or any(w == s for s in seen):
                continue
            seen.append(w)
            d = self.attrs(w, ["AXPosition", "AXSize", "AXMinimized"])
            r = self.rect(d) if d.get("AXMinimized") is not True else None
            if r is None or not rect_ok(r, self.sw, self.sh):
                continue
            sized.append((r[2] * r[3], len(sized), w))
        sized.sort(key=lambda e: (e[0], e[1]))
        small = [w for a, _, w in sized if fa is None or a < fa]
        big = [w for a, _, w in sized if fa is not None and a >= fa]
        return small, big


def _ensure_setup(AX, app, pid: int):
    global _timeout_set
    if not _timeout_set:
        _timeout_set = True
        try:  # 系统级元素上设：对本进程所有 AX 调用生效
            AX.AXUIElementSetMessagingTimeout(AX.AXUIElementCreateSystemWide(), TIMEOUT_S)
        except Exception:  # noqa: BLE001
            pass
    AX.AXUIElementSetMessagingTimeout(app, TIMEOUT_S)
    if pid not in _manual_done:
        _manual_done.add(pid)
        try:  # 让 Electron（VS Code）/ Chrome 暴露网页和界面内容
            AX.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
        except Exception:  # noqa: BLE001
            pass


def _collect(app_name: str | None, screen_wh: tuple[float, float] | None) -> tuple[str, list[dict]]:
    import ApplicationServices as AX
    import AppKit  # noqa: F401  第一次 import 要 ~200 ms，不算进 150 ms 预算
    import Quartz  # noqa: F401

    t0 = time.perf_counter()
    if not AX.AXIsProcessTrusted():
        return "", []
    tgt = _target(app_name)
    if tgt is None:
        return "", []
    pid, name = tgt
    sw, sh = screen_wh or _screen_wh()
    if not (sw > 0 and sh > 0):
        return name, []
    app = AX.AXUIElementCreateApplication(pid)
    _ensure_setup(AX, app, pid)
    wk = _Walker(AX, (sw, sh), t0 + BUDGET_S)

    # 0. Dock（ax_snap 已经会读；先读，免得窗口太大把时间用完）。自动隐藏时在屏幕外 → 丢掉
    for title, rect in ax_snap.dock_items():
        if rect_ok(rect, sw, sh):
            wk.out.append(("AXDockItem", pick_name({"AXTitle": title}.get), to_box(rect, sw, sh)))

    # 顺序：小而要紧的先读（菜单、菜单栏、对话框），最后是可能很大的主窗口，超时就停在那里
    d = wk.attrs(app, ["AXFocusedWindow", "AXMainWindow", "AXWindows", "AXChildren", "AXMenuBar"])
    focused = d.get("AXFocusedWindow") or d.get("AXMainWindow")
    # 1. 打开着的右键菜单 / 弹出菜单（App 的直接子元素）
    kids = []
    for c in d.get("AXChildren") or []:
        if wk.over():
            break
        role = wk.attr(c, "AXRole")
        if role == "AXMenu":
            wk.walk(c, 0)
        elif role == "AXWindow":
            kids.append(c)
    # 2. 菜单栏：顶层项（正打开的那个菜单会往下走）
    bar = d.get("AXMenuBar")
    if bar is not None:
        for item in wk.attr(bar, "AXChildren") or []:
            if wk.over():
                break
            wk.walk(item, 1)
    # 3. 这个 App 在屏幕上的其它窗口（不只对话框：Zoom 的时间下拉列表是单独的小窗口），小的先读
    small, big = wk.other_windows(list(d.get("AXWindows") or []) + kids, focused)
    for w in small:
        if wk.over():
            break
        wk.window(w, main=False)
    # 4. 聚焦的窗口（没有就主窗口），里面的 sheet 也在它的子元素里
    if focused is not None:
        wk.window(focused)
    # 5. 不比聚焦窗口小的别的窗口（另一个文档窗口）：还有时间才读，当主窗口对待（不收标签）
    for w in big:
        if wk.over():
            break
        wk.window(w)
    return name, finalize(wk.out)


def collect(app_name: str | None, screen_wh: tuple[float, float] | None = None, log: bool = True) -> list[dict]:
    """目标 App 现在能点的控件：[{"id", "role", "name", "box"}]。没权限 / 出错 / 什么都没有 → []。永不抛异常。"""
    global _last_stats
    t0 = time.perf_counter()
    try:
        name, els = _collect(app_name, screen_wh)
    except Exception as e:  # noqa: BLE001
        if log:
            print(f"[ax] 读取控件失败：{e}", flush=True)
        return []
    _last_stats = stats_line(name or str(app_name), els, (time.perf_counter() - t0) * 1000)
    if log:
        print(_last_stats, flush=True)
    return els


# ================= 焦点（/api/next-step 的 confirmed.detail）=================
FOCUS_BUDGET_S = 0.05


def focus_info(app_name: str | None) -> tuple[str | None, str | None, str | None] | None:
    """目标 App 现在聚焦的控件 (role, name) 和聚焦窗口的标题。没权限 / 出错 / 超时 → None。永不抛异常。"""
    try:
        import ApplicationServices as AX

        t0 = time.perf_counter()
        if not AX.AXIsProcessTrusted():
            return None
        tgt = _target(app_name)
        if tgt is None:
            return None
        app = AX.AXUIElementCreateApplication(tgt[0])
        _ensure_setup(AX, app, tgt[0])
        wk = _Walker(AX, (1, 1), t0 + FOCUS_BUDGET_S)
        d = wk.attrs(app, ["AXFocusedUIElement", "AXFocusedWindow"])
        role = name = title = None
        el = d.get("AXFocusedUIElement")
        if el is not None and not wk.over():
            fd = wk.attrs(el, ["AXRole", *NAME_ATTRS, "AXTitleUIElement"])
            role = str(fd.get("AXRole") or "") or None
            name = wk.name(fd)
        win = d.get("AXFocusedWindow")
        if win is not None and not wk.over():
            v = wk.attr(win, "AXTitle")
            title = " ".join(v.split())[:NAME_MAX] if isinstance(v, str) and v.strip() else None
        return role, name, title
    except Exception:  # noqa: BLE001
        return None


def focus_detail(info, prev_title: str | None) -> str | None:
    """focus_info 的结果 → "focused AXComboBox '开始时间'"、"new window 'Google Calendar'"（和上一次请求时的窗口标题不一样）。"""
    if not info:
        return None
    role, name, title = info
    parts = []
    if role:
        parts.append(f"focused {role}" + (f" {name!r}" if name else ""))
    if title and prev_title is not None and title != prev_title:
        parts.append(f"new window {title!r}")
    return "; ".join(parts) or None
