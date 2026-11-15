"""当前最前面的 App 名字（发给后端的 frontmost_app，让它知道用户在教哪个软件）。

用户点 ShowMe 面板打字时，最前面的是 ShowMe 自己（显示为 Python）——这时返回上一次记住的那个 App。
控制器每秒调一次 remember()，所以用户一打开 ShowMe 就直接点面板，也能拿到之前在用的 App。
"""

import os

_last: str | None = None


def _read() -> tuple[str | None, int | None]:
    """(App 名字, 进程号)。单独拆出来，测试时可以替换。"""
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None, None
    name = app.localizedName()
    return (str(name) if name else None), int(app.processIdentifier())


def frontmost_app() -> str | None:
    """最前面的 App；是 ShowMe 自己就返回上一次记住的；出错返回 None。"""
    global _last
    try:
        name, pid = _read()
    except Exception as e:  # noqa: BLE001  不是 macOS / PyObjC 出问题都不能崩
        print(f"[apps] 读不到最前面的 App：{e}")
        return None
    if not name or pid == os.getpid() or name == "ShowMe":
        return _last
    _last = name
    return name


def remember() -> None:
    """只为了更新「上一次记住的 App」。"""
    frontmost_app()
