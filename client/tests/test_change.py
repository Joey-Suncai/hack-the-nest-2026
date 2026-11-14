import numpy as np

from showme.change import ChangeDetector, mask_rects

Z, O = np.zeros((64, 64), np.float32), np.ones((64, 64), np.float32)


def test_change_then_stable_fires_once():
    d = ChangeDetector(0.004, 2)
    d.reset(Z)
    assert [d.feed(Z), d.feed(O), d.feed(O), d.feed(O), d.feed(O)] == [False, False, False, True, False]


def test_small_noise_ignored():
    d = ChangeDetector(0.004, 2)
    d.reset(Z)
    noisy = Z.copy()
    noisy[0, :10] = 1.0  # 平均差约 0.002
    assert not any(d.feed(noisy) for _ in range(5))


def test_static_overlay_is_not_change():
    base = Z.copy()
    base[10:20, 10:20] = 1.0  # 基准帧里已经有高亮框
    d = ChangeDetector(0.004, 2)
    d.reset(base)
    assert not any(d.feed(base) for _ in range(5))


def test_mask_hides_overlay_region():
    frame = Z.copy()
    frame[16:32, 16:32] = 1.0  # 屏幕 1/4–1/2 处的「呼吸光圈」
    masked = mask_rects(frame, [(250, 250, 250, 250)], 1000, 1000)
    assert float(masked.max()) == 0.0


def test_small_menu_is_detected():
    """弹出一个小菜单（约占屏幕 3%）也要能检测到——整屏平均差只有 0.012，旧算法会漏。"""
    menu = Z.copy()
    menu[10:26, 20:28] = 0.4
    d = ChangeDetector(0.004, 2)
    d.reset(Z)
    assert [d.feed(menu) for _ in range(4)] == [False, False, True, False]
