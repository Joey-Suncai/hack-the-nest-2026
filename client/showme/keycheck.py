"""关键指标检查：/api/next-step 返回的 `check` 说「这一步做完后什么应该成立」，这里精确、便宜地判断它。

check = {"kind": "value_contains" | "appears" | "disappears" | "selected",
         "element_id": int | None, "label": str | None, "expect": str | None, "box": [ymin, xmin, ymax, xmax] | None}

快照 Snap：{规范化的控件名 → (值, 是否选中)} + 所有名字 / 值 / 窗口标题的集合。来源 AX（辅助功能）或 OCR（框里的文字）。
evaluate(check, baseline, now) → "met" | "not_met" | "unknown"。unknown = 没法判断，调用方什么都不做。
纯函数部分不碰 AX / Qt，可测试；read_state / read_ocr 任何失败都返回 None，绝不抛异常。
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Callable, NamedTuple

MET, NOT_MET, UNKNOWN = "met", "not_met", "unknown"
KINDS = ("value_contains", "appears", "disappears", "selected")
BUDGET_S = 0.1  # 一次 AX 读取最多用多久
MAX_NODES = 3000
MAX_DEPTH = 40
VALUE_MAX = 120
LABEL_ATTRS = ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp")
TOGGLE_ROLES = {"AXCheckBox", "AXRadioButton", "AXSwitch", "AXMenuItem", "AXTab"}
_ATTRS = ["AXRole", *LABEL_ATTRS, "AXValue", "AXTitleUIElement", "AXSelected", "AXChildren", "AXHidden", "AXExpanded"]
_MONTHS = {m: i for i, ms in enumerate((("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
                                         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
                                         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
                                         ("dec", "december")), 1) for m in ms}


class Snap(NamedTuple):
    values: dict  # 规范化的名字 → (值, 选中)
    names: frozenset  # 规范化的名字 / 值 / 窗口标题（appears / disappears 用）
    complete: bool = True  # False：读到一半超时了（「不见了」不能下结论）
    src: str = "AX"


# ================= 文字规范化 =================
def norm(s) -> str:
    """NFKC（全角数字 → 半角）、小写、合并空白。"""
    return " ".join(unicodedata.normalize("NFKC", str(s or "")).lower().split())


def _squash(s: str) -> str:
    return "".join(norm(s).split())


def _tokens(s: str) -> list[str]:
    return re.findall(r"\d+|[^\W\d_]+", norm(s))


def _numbers(s: str) -> list[int]:
    """数字序列，月份名也算数字："Nov 16, 2026" → [11, 16, 2026]；"2026/11/06" → [2026, 11, 6]。"""
    out = []
    for t in _tokens(s):
        if t.isdigit():
            out.append(int(t))
        elif t in _MONTHS:
            out.append(_MONTHS[t])
    return out


def _sub(short: list, long_: list) -> bool:
    n = len(short)
    return n > 0 and any(long_[i:i + n] == short for i in range(len(long_) - n + 1))


def contains(value, expect) -> bool:
    """value 里有没有 expect：不分大小写、不管空白；有数字时比数字序列（"11/16" ⊂ "2026/11/16"、"Nov 16"）。"""
    v, e = _squash(value), _squash(expect)
    if not e:
        return False
    if e in v:
        return True
    want = _numbers(expect)
    if not want or not _sub(want, _numbers(value)):
        return False
    words = [t for t in _tokens(expect) if not t.isdigit() and t not in _MONTHS]  # 数字之外的字也要在
    return all(w in v for w in words)


# ================= check 本身 =================
def parse(check) -> dict | None:
    """后端给的 check → 规范的 dict；格式不对 / kind 不认识 → None（当没给）。"""
    if not isinstance(check, dict) or check.get("kind") not in KINDS:
        return None
    s = lambda k: (str(check[k]).strip() or None) if isinstance(check.get(k), (str, int, float)) else None  # noqa: E731
    box = check.get("box")
    box = [int(v) for v in box] if isinstance(box, (list, tuple)) and len(box) == 4 and \
        all(isinstance(v, (int, float)) for v in box) and box[2] > box[0] and box[3] > box[1] else None
    eid = check.get("element_id")
    return {"kind": check["kind"], "element_id": eid if isinstance(eid, int) else None,
            "label": s("label"), "expect": s("expect"), "box": box}


def target(check: dict) -> str | None:
    """appears / disappears 找的名字。"""
    return check.get("expect") or check.get("label")


def summary(check: dict) -> str:
    k, label, exp = check["kind"], check.get("label"), check.get("expect")
    if k == "value_contains":
        return f"value_contains {label} ⊇ {exp!r}" if exp else f"value_contains {label} changed"
    if k == "selected":
        return f"selected {label}"
    return f"{k} {target(check)!r}"


def lookup(snap: Snap | None, label: str | None):
    """按名字找控件：完全一样 > 包含（较短的至少 2 个字，选名字最短的）。找不到 → None。"""
    if snap is None or not label:
        return None
    want = _squash(label)
    if not want:
        return None
    vals = {_squash(k): v for k, v in snap.values.items()}
    if want in vals:
        return vals[want]
    cands = [(len(k), v) for k, v in vals.items() if min(len(k), len(want)) >= 2 and (want in k or k in want)]
    return min(cands, key=lambda c: c[0])[1] if cands else None


def present(snap: Snap | None, name: str | None) -> bool:
    w = _squash(name)
    return bool(snap and w) and any(w in _squash(n) for n in snap.names)


def evaluate(check, baseline: Snap | None, now: Snap | None) -> str:
    """这一步的关键指标现在成立了没有。读不到 / 找不到 / 没法比 → unknown。"""
    c = parse(check)
    if c is None or now is None:
        return UNKNOWN
    k = c["kind"]
    if k == "value_contains":
        cur = lookup(now, c["label"])
        if cur is None:
            return UNKNOWN
        if c["expect"]:
            return MET if contains(cur[0], c["expect"]) else NOT_MET
        base = lookup(baseline, c["label"])
        if base is None:
            return UNKNOWN
        return MET if norm(cur[0]) and norm(cur[0]) != norm(base[0]) else NOT_MET
    if k == "selected":
        if now.src != "AX":
            return UNKNOWN  # OCR 看不出选中
        cur = lookup(now, c["label"])
        if cur is None:
            return UNKNOWN
        base = lookup(baseline, c["label"])
        return MET if cur[1] and not (base and base[1]) else NOT_MET
    name = target(c)
    if not name:
        return UNKNOWN
    if k == "appears":
        return MET if present(now, name) else NOT_MET
    # disappears：基准里得有，现在没了才算；读到一半超时的「没了」不算
    if baseline is None or not present(baseline, name):
        return UNKNOWN
    if present(now, name):
        return NOT_MET
    return MET if now.complete else UNKNOWN


def describe(check: dict, snap: Snap | None) -> str:
    """日志 / confirmed.detail 用："开始日期 = 2026/11/16"、"'新建事件' present"。"""
    c = parse(check)
    if c is None or snap is None:
        return ""
    if c["kind"] in ("value_contains", "selected"):
        cur = lookup(snap, c["label"])
        if cur is None:
            return f"{c['label']} not found"
        return f"{c['label']} = {cur[0]}" + (" (selected)" if c["kind"] == "selected" and cur[1] else "")
    name = target(c)
    return f"{name!r} {'present' if present(snap, name) else 'absent'}"


def usable(check: dict, snap: Snap | None) -> bool:
    """AX 快照能不能用来判断这个 check（否则看框里的 OCR）。"""
    if snap is None:
        return False
    k = check["kind"]
    if k in ("value_contains", "selected"):
        return lookup(snap, check.get("label")) is not None
    if k == "disappears":
        return present(snap, target(check))
    return True  # appears：现在没有是正常的


# ================= OCR 后备 =================
def crop_box(img, box):
    """[ymin, xmin, ymax, xmax] 0–1000 → 截图里那块（截图是逻辑尺寸还是像素都一样）。"""
    w, h = img.size
    y0, x0, y1, x1 = box
    l, t, r, b = int(x0 / 1000 * w), int(y0 / 1000 * h), int(x1 / 1000 * w + 0.999), int(y1 / 1000 * h + 0.999)
    return img.crop((max(0, l), max(0, t), min(w, max(l + 1, r)), min(h, max(t + 1, b))))


def read_ocr(img, check: dict, find: Callable | None = None) -> Snap | None:
    """框里的文字当快照：label → (框里全部文字, False)；names = 每一行 + 全部文字。失败 → None。"""
    try:
        if img is None or not check.get("box"):
            return None
        if find is None:
            from showme import ocr

            find = ocr.find_text_boxes
        part = crop_box(img, check["box"])
        lines = [str(t) for t, _b in (find(part, None, fast=False) or []) if str(t).strip()]
        text = " ".join(" ".join(lines).split())
        key = norm(check.get("label") or "box")
        return Snap({key: (text, False)}, frozenset(norm(s) for s in lines + [text] if s), True, "OCR")
    except Exception as e:  # noqa: BLE001
        print(f"[check] OCR 读取失败：{e}", flush=True)
        return None


# ================= AX =================
def _str(v) -> str | None:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    if isinstance(v, str):
        s = " ".join(v.split())
        return s[:VALUE_MAX] if s else None
    return None


def node_entry(role: str, get: Callable) -> tuple[list[str], str, bool]:
    """一个节点 → (它的名字们, 值, 选中)。值 = AXValue；没有值时用名字。get(attr) 返回属性值或 None。"""
    labels = []
    for a in (*LABEL_ATTRS, "AXTitleUIElement.value"):
        s = _str(get(a))
        if s and not s.isdigit() and s not in labels:
            labels.append(s)
    raw = get("AXValue")
    val = _str(raw)
    sel = get("AXSelected") is True or (role in TOGGLE_ROLES and raw is not None and _str(raw) == "1")
    if val is None or (role in TOGGLE_ROLES and val in ("0", "1")):
        val = " ".join(labels) if val is None else val
    return labels, val or "", sel


class _Reader:
    def __init__(self, AX, wk, label: str | None, stop_on_label: bool):
        self.AX, self.wk = AX, wk
        self.values: dict = {}
        self.names: set = set()
        self.want = _squash(label) if label else None
        self.stop_on_label = stop_on_label
        self.found = False

    def done(self) -> bool:
        return self.wk.over() or (self.stop_on_label and self.found)

    def node(self, el, depth: int = 0):
        wk = self.wk
        if depth > MAX_DEPTH or self.done():
            return
        wk.nodes += 1
        d = wk.attrs(el, _ATTRS)
        if not d or d.get("AXHidden") is True:
            return
        role = str(d.get("AXRole") or "")

        def get(a):
            if a == "AXTitleUIElement.value":
                t = d.get("AXTitleUIElement")
                return (wk.attr(t, "AXValue") or wk.attr(t, "AXTitle")) if t is not None else None
            return d.get(a)

        labels, val, sel = node_entry(role, get)
        for lb in labels:
            self.values.setdefault(norm(lb), (val, sel))
            self.names.add(norm(lb))
            if self.want and _squash(lb) == self.want:
                self.found = True
        if val:
            self.names.add(norm(val))
        if d.get("AXExpanded") is False and role in ("AXDisclosureTriangle", "AXOutline"):
            return
        if role == "AXMenuBarItem" and d.get("AXSelected") is not True:
            return  # 没打开的菜单
        for c in d.get("AXChildren") or []:
            if self.done():
                return
            self.node(c, depth + 1)


def read_state(app_name: str | None, check: dict | None, screen_wh=None) -> Snap | None:
    """目标 App 现在所有控件的名字 / 值 / 选中状态 + 窗口标题。check 是 value_contains / selected 时，
    找到那个控件就停。约 BUDGET_S 秒；没权限 / 出错 → None。永不抛异常。"""
    try:
        import ApplicationServices as AX

        from showme import ax_tree

        t0 = time.perf_counter()
        if not AX.AXIsProcessTrusted():
            return None
        tgt = ax_tree._target(app_name)
        if tgt is None:
            return None
        app = AX.AXUIElementCreateApplication(tgt[0])
        ax_tree._ensure_setup(AX, app, tgt[0])
        wk = ax_tree._Walker(AX, screen_wh or (1, 1), t0 + BUDGET_S)
        c = parse(check) if check else None
        label = c.get("label") if c else None
        r = _Reader(AX, wk, label, bool(c and c["kind"] in ("value_contains", "selected") and label))
        d = wk.attrs(app, ["AXFocusedWindow", "AXWindows", "AXChildren"])
        focused = d.get("AXFocusedWindow")
        wins = ([focused] if focused is not None else []) + [w for w in (d.get("AXWindows") or []) if w != focused]
        for w in wins:
            if r.done():
                break
            title = _str(wk.attr(w, "AXTitle"))
            if title:
                r.names.add(norm(title))
            r.node(w)
        for k in d.get("AXChildren") or []:  # 菜单栏、打开着的菜单（窗口上面已经读过）
            if r.done():
                break
            if wk.attr(k, "AXRole") != "AXWindow":
                r.node(k)
        complete = not wk.over() or r.found
        return Snap(r.values, frozenset(r.names), complete, "AX")
    except Exception as e:  # noqa: BLE001
        print(f"[check] AX 读取失败：{e}", flush=True)
        return None


def probe(check: dict, src: str | None, app: str | None, screen_wh, grab: Callable | None = None,
          find: Callable | None = None) -> Snap | None:
    """按 src 读一张快照：AX → read_state；OCR → 截图后读框里的字。"""
    if src == "AX":
        return read_state(app, check, screen_wh)
    if src == "OCR" and check.get("box"):
        try:
            if grab is None:
                from showme import capture

                grab = capture.grab
            img = grab()
        except Exception as e:  # noqa: BLE001
            print(f"[check] 截图失败：{e}", flush=True)
            return None
        return read_ocr(img, check, find)
    return None


def baseline(check: dict, app: str | None, screen_wh, ax_snap: Snap | None = None, grab=None, find=None):
    """取基准快照并决定以后用哪种来源：AX 能判断就用 AX，否则框里 OCR，都不行 → (None, None)。
    ax_snap：发请求时已经读好的全量 AX 快照（用户在等 Gemini 时动了手，这张才是真正「做之前」的）。"""
    snap = ax_snap
    if not usable(check, snap):
        snap = read_state(app, check, screen_wh)  # 请求时那张没读到（超时 / 没有）：现在再读一次
    if usable(check, snap):
        return snap, "AX"
    if check.get("box") and check["kind"] != "selected":
        o = probe(check, "OCR", app, screen_wh, grab, find)
        if o is not None:
            return o, "OCR"
    return None, None
