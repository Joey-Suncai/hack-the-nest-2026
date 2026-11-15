from PIL import Image, ImageDraw, ImageFont

from showme.ocr import best_match, find_text_boxes, locate, vision_to_box


def test_vision_to_box_flips_origin():
    assert vision_to_box(0.1, 0.2, 0.3, 0.4) == [400, 100, 800, 400]


ITEMS = [("Chart", [100, 100, 140, 200]), ("Chart Style", [800, 800, 840, 900]),
         ("Clone Git Repository...", [300, 200, 330, 400])]


def test_exact_match_case_insensitive():
    assert best_match(ITEMS, "chart") == [100, 100, 140, 200]


def test_contains_prefers_shortest_line():
    assert best_match([("Chart Style", [1, 1, 2, 2]), ("Insert Chart", [3, 3, 4, 4]), ("A long line about the Chart menu", [5, 5, 6, 6])], "chart") == [1, 1, 2, 2]


def test_ellipsis_and_fuzzy():
    assert best_match(ITEMS, "Clone Git Repository…") == [300, 200, 330, 400]
    assert best_match(ITEMS, "Clone Git Repo") == [300, 200, 330, 400]


def test_no_match_returns_none():
    assert best_match(ITEMS, "Table") is None
    assert best_match(ITEMS, None) is None
    assert best_match([], "Chart") is None


def test_real_vision_finds_rendered_text():
    """真的跑一次 macOS Vision：画一张带按钮文字的图，能找到并给出大致位置。"""
    img = Image.new("RGB", (1200, 700), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
    except OSError:
        font = ImageFont.load_default()
    d.text((100, 100), "Open Folder", fill="black", font=font)
    d.text((700, 500), "Clone Git Repository", fill="black", font=font)
    texts = [t for t, _ in find_text_boxes(img)]
    assert any("Clone Git Repository" in t for t in texts), texts
    box, ms = locate(img, "clone git repository")
    assert box is not None
    ymin, xmin, ymax, xmax = box
    assert 650 < (ymin + ymax) / 2 < 790 and 550 < (xmin + xmax) / 2 < 950, box  # 图上 (700..1100, 500..540)
