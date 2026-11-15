from PIL import Image, ImageDraw, ImageFont

from showme.ocr import best_match, find_text_boxes, locate, vision_to_box


def test_vision_to_box_flips_origin():
    assert vision_to_box(0.1, 0.2, 0.3, 0.4) == [400, 100, 800, 400]


ITEMS = [("Chart", [100, 100, 140, 200]), ("Chart Style", [800, 800, 840, 900]),
         ("Clone Git Repository...", [300, 200, 330, 400])]


def test_exact_match_case_insensitive():
    assert best_match(ITEMS, "chart") == [100, 100, 140, 200]


def test_contains_prefers_shortest_line_when_not_strict():
    items = [("Chart Style", [1, 1, 2, 2]), ("Insert Chart", [3, 3, 4, 4]), ("A long line about the Chart menu", [5, 5, 6, 6])]
    assert best_match(items, "chart", strict=False) == [1, 1, 2, 2]
    assert best_match(items, "chart") is None  # 秒出框只认完全一样的


def test_ellipsis_and_fuzzy():
    assert best_match(ITEMS, "Clone Git Repository…") == [300, 200, 330, 400]  # 省略号不算差别
    assert best_match(ITEMS, "Clone Git Repo") is None
    assert best_match(ITEMS, "Clone Git Repo", strict=False) == [300, 200, 330, 400]


def test_strict_rejects_ambiguous_duplicates():
    """联调实例：屏幕上有两处 "Extensions"（左边栏提示 + 右边标题）→ 不秒出，等 Gemini。"""
    items = [("Extensions", [207, 894, 220, 933]), ("EXTENSIONS", [100, 60, 112, 120]), ("Install", [201, 110, 213, 134])]
    assert best_match(items, "Extensions") is None
    assert best_match(items, "install") == [201, 110, 213, 134]


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


def test_normalized_exact_ignores_case_and_edge_symbols():
    items = [("R Markdown...", [10, 10, 20, 60]), ("Next Section →", [500, 300, 520, 400]), ('"Save"', [1, 1, 2, 2])]
    assert best_match(items, "R Markdown") == [10, 10, 20, 60]
    assert best_match(items, "r markdown…") == [10, 10, 20, 60]
    assert best_match(items, "Next Section") == [500, 300, 520, 400]
    assert best_match(items, "NEXT SECTION →") == [500, 300, 520, 400]
    assert best_match(items, "save:") == [1, 1, 2, 2]


def test_contains_either_way_whole_words_only():
    assert best_match([("R Markdown Document", [1, 1, 2, 2])], "R Markdown...") == [1, 1, 2, 2]  # 屏幕上的更长
    assert best_match([("Clone", [1, 1, 2, 2])], "Clone Repository") == [1, 1, 2, 2]  # label 更长
    assert best_match([("Encode", [1, 1, 2, 2])], "code") is None  # 不是整词
    assert best_match([("Clone Git Repository", [1, 1, 2, 2])], "Clone Git Repo") is None


def test_too_short_does_not_contain_match():
    assert best_match([("OK Cancel", [1, 1, 2, 2])], "OK") is None  # 只有 2 个字符：不做包含匹配
    assert best_match([("OK", [1, 1, 2, 2])], "ok") == [1, 1, 2, 2]  # 完全相同可以


def test_exact_beats_contains_and_nearest_breaks_ties():
    items = [("Save", [100, 100, 110, 140]), ("Save As", [100, 200, 110, 260])]
    assert best_match(items, "Save") == [100, 100, 110, 140]  # 完全相同优先
    two = [("Extensions", [207, 894, 220, 933]), ("EXTENSIONS", [100, 60, 112, 120])]
    assert best_match(two, "Extensions") is None  # 分不出来 → 等 Gemini
    assert best_match(two, "Extensions", near=[90, 20, 110, 40]) == [100, 60, 112, 120]  # 离上一个框近
    mid = [("Open", [500, 100, 510, 140]), ("Open", [500, 860, 510, 900])]
    assert best_match(mid, "Open", near=[490, 480, 510, 520]) is None  # 一样远：还是放弃
