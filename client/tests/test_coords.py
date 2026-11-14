from showme.coords import box_to_rect, expand


def test_box_to_rect_uses_logical_size():
    assert box_to_rect([250, 500, 750, 1000], 1440, 900) == (720, 225, 720, 450)


def test_expand():
    assert expand((100, 100, 50, 20), 12) == (88, 88, 74, 44)
