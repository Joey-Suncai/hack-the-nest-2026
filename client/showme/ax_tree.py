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
DIALOG_SUBROLES = {"AXDialog", "AXSystemDialog", "AXFloatingWindow"}
COLLAPSIBLE_ROLES = {"AXDisclosureTriangle", "AXOutline"}  # 折叠后子元素真的不在屏幕上的控件
CLIP_ROLES = {"AXRow", "AXCell"}  # 只有这些会滚出窗口，要按窗口框裁
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

    def walk(self, el, depth: int = 0, clip: Rect | None = None, children: bool = True):
        if depth > MAX_DEPTH or self.over():
            return
        self.nodes += 1
        d = self.attrs(el, _ATTRS)
        if not d or d.get("AXHidden") is True:
            return
        role = str(d.get("AXRole") or "")
        if role in ROLES:
            self.add(role, d, clip)
        # Chromium / Electron（Chrome、VS Code）把普通按钮、容器也报成 AXExpanded=False，
        # 只有真正能折叠的控件才认这个标记，否则整个窗口都读不进来（实测 VS Code 53 个只剩 0 个）
        if not children or (d.get("AXExpanded") is False and role in COLLAPSIBLE_ROLES):
            return
        if role == "AXMenuBarItem" and d.get("AXSelected") is not True:
            return  # 没打开的菜单：里面的菜单项不在屏幕上
        for c in d.get("AXChildren") or []:
            self.walk(c, depth + 1, clip)

    def window(self, w):
        d = self.attrs(w, ["AXPosition", "AXSize", "AXMinimized"])
        if d.get("AXMinimized") is True:
            return
        r = self.rect(d)
        if r is None or not rect_ok(r, self.sw, self.sh):
            return
        self.walk(w, 0, clip=r)


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
    for c in d.get("AXChildren") or []:
        if wk.over():
            break
        if wk.attr(c, "AXRole") == "AXMenu":
            wk.walk(c, 0)
    # 2. 菜单栏：顶层项（正打开的那个菜单会往下走）
    bar = d.get("AXMenuBar")
    if bar is not None:
        for item in wk.attr(bar, "AXChildren") or []:
            if wk.over():
                break
            wk.walk(item, 1)
    # 3. 这个 App 在屏幕上的对话框 / 浮动窗口（不是聚焦的那个）
    for w in d.get("AXWindows") or []:
        if wk.over():
            break
        if focused is not None and w == focused:
            continue
        if wk.attr(w, "AXSubrole") in DIALOG_SUBROLES or wk.attr(w, "AXModal") is True:
            wk.window(w)
    # 4. 聚焦的窗口（没有就主窗口），里面的 sheet 也在它的子元素里
    if focused is not None:
        wk.window(focused)
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
