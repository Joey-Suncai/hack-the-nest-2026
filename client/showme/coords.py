"""0–1000 归一化框 → 屏幕逻辑坐标。"""


def box_to_rect(box: list[int], screen_w: float, screen_h: float) -> tuple[int, int, int, int]:
    """[ymin, xmin, ymax, xmax]（0–1000，相对整张截图）→ (x, y, w, h)，主屏逻辑坐标。"""
    ymin, xmin, ymax, xmax = box
    x = round(xmin / 1000 * screen_w)
    y = round(ymin / 1000 * screen_h)
    w = round((xmax - xmin) / 1000 * screen_w)
    h = round((ymax - ymin) / 1000 * screen_h)
    return x, y, w, h


def expand(rect: tuple[int, int, int, int], px: int) -> tuple[int, int, int, int]:
    """四周各扩 px。"""
    x, y, w, h = rect
    return x - px, y - px, w + 2 * px, h + 2 * px
