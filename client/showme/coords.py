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


def same_target(a: list[int] | None, b: list[int] | None, min_iou: float = 0.3) -> bool:
    """两个 0–1000 框是不是指同一个元素：IoU 够大，或者一个框的中心落在另一个框里。

    OCR 框只包住文字，Gemini 框常常包住整个按钮，所以光看 IoU 会误判成「不一样」。
    """
    if not a or not b:
        return False
    ay1, ax1, ay2, ax2 = a
    by1, bx1, by2, bx2 = b
    iw = max(0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    if union > 0 and inter / union >= min_iou:
        return True

    def inside(box, y, x):
        return box[0] <= y <= box[2] and box[1] <= x <= box[3]

    return inside(b, (ay1 + ay2) / 2, (ax1 + ax2) / 2) or inside(a, (by1 + by2) / 2, (bx1 + bx2) / 2)
