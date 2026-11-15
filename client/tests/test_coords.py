from showme.coords import box_to_rect, expand


def test_box_to_rect_uses_logical_size():
    assert box_to_rect([250, 500, 750, 1000], 1440, 900) == (720, 225, 720, 450)


def test_expand():
    assert expand((100, 100, 50, 20), 12) == (88, 88, 74, 44)


def test_same_target():
    from showme.coords import same_target

    ocr_text = [300, 210, 320, 300]  # 只包住文字
    gemini_button = [292, 190, 330, 320]  # 包住整个按钮
    assert same_target(ocr_text, gemini_button)
    assert same_target([100, 100, 200, 200], [110, 110, 210, 210])
    assert not same_target([100, 100, 140, 200], [600, 600, 650, 700])
    assert not same_target(None, [1, 1, 2, 2])
