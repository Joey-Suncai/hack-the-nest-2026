"""/api/next-step 的几个可选字段怎么算（纯函数，不碰 AX / Qt，可测试）：deep、confirmed、app_switched_from。
接口见 docs/API.md。"""

SKIP_FRONT = ("showme", "terminal", "claude", "python")  # 用户在这些里跟 ShowMe 说话 / 启动它：不算「切到别的软件」
CONFIRM_KINDS = {"click": "click", "typing": "typing", "enter": "enter"}  # _step_completed 的 reason → confirmed.kind


def skipped_app(name: str | None) -> bool:
    n = (name or "").strip().lower()
    return not n or n.startswith(SKIP_FRONT) or n in ("iterm2", "iterm")  # "python3.12"、"Claude Code" 也算


def real_front(front: str | None) -> str | None:
    """最前面的 App；是 ShowMe / 终端 / Claude / Python → None。"""
    return None if skipped_app(front) else front


def switched_from(prev: str | None, front: str | None) -> str | None:
    """上一次请求时最前面的 App 和这次不一样 → 返回上一次的。两边有一个是跳过的 App / 读不到 → None。"""
    prev, front = real_front(prev), real_front(front)
    if prev and front and prev != front:
        return prev
    return None


def ax_app(front: str | None, task_app: str | None) -> str | None:
    """读哪个 App 的控件：最前面的那个（流程可能已经跳到别的软件了）；它是跳过的 App 时用任务的软件。"""
    return real_front(front) or task_app


def confirmed(reason: str | None, target: str | None, detail: str | None = None) -> dict | None:
    """客户端确认用户做了上一步 → confirmed 字段；画面变化 / 点在框外附近 / 按快捷键 → None（不带）。"""
    kind = CONFIRM_KINDS.get(reason or "")
    if kind is None:
        return None
    return {"kind": kind, "target": target or None, "detail": detail or None}


def _same(a, b) -> bool:
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()


def repeated(recent: list[tuple[str | None, str | None]]) -> bool:
    """最近两次出框的是同一句指令或同一个目标。recent = [(instruction, target_label)]，最新的在后。"""
    if len(recent) < 2:
        return False
    (i1, t1), (i2, t2) = recent[-2], recent[-1]
    return _same(i1, i2) or _same(t1, t2)


def want_deep(history: list, app_switched: str | None, last: dict | None, user_note: str | None,
              recent: list[tuple[str | None, str | None]]) -> bool:
    """要不要让后端多想一下：刚开始 / 换了软件 / 上次 unsure 或 previous_ok=false / 同一步出了两次 / 用户说了话。"""
    if not history or app_switched or user_note:
        return True
    if isinstance(last, dict) and (last.get("status") == "unsure" or last.get("previous_ok") is False):
        return True
    return repeated(recent)


def plan_of(r: dict) -> list[str] | None:
    """返回里的 plan：非空的字符串列表才用。"""
    p = r.get("plan") if isinstance(r, dict) else None
    if not isinstance(p, list):
        return None
    p = [s.strip() for s in p if isinstance(s, str) and s.strip()]
    return p or None


def reason_context(r: dict) -> str | None:
    """给 Agent 的背景消息：[step] Situation: … Why: …；两个都没有 → None。"""
    if not isinstance(r, dict):
        return None
    s, why = str(r.get("situation") or "").strip(), str(r.get("reasoning") or "").strip()
    if not (s or why):
        return None
    parts = (["Situation: " + s] if s else []) + (["Why: " + why] if why else [])
    return "[step] " + " ".join(parts)
