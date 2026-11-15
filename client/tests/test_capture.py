import base64
import io

from PIL import Image

from showme.capture import to_jpeg_b64, to_small_gray


def test_jpeg_b64_resizes_long_side():
    img = Image.new("RGB", (2880, 1800), "white")
    out = Image.open(io.BytesIO(base64.b64decode(to_jpeg_b64(img))))
    assert out.size == (1280, 800)


def test_jpeg_b64_keeps_small_image():
    out = Image.open(io.BytesIO(base64.b64decode(to_jpeg_b64(Image.new("RGB", (800, 500))))))
    assert out.size == (800, 500)


def test_small_gray_shape_and_range():
    a = to_small_gray(Image.new("RGB", (2880, 1800), "white"))
    assert a.shape == (64, 64) and float(a.max()) <= 1.0


def test_grab_uses_window_exclusion_when_registered(monkeypatch):
    from showme import capture

    seen = []
    monkeypatch.setattr(capture, "EXCLUDE_WINDOW_IDS", {42})
    monkeypatch.setattr(capture, "_grab_excluding", lambda ex: seen.append(set(ex)) or Image.new("RGB", (10, 10)))
    assert capture.grab().size == (10, 10) and seen == [{42}]


def test_grab_falls_back_when_exclusion_fails(monkeypatch):
    from showme import capture

    monkeypatch.setattr(capture, "EXCLUDE_WINDOW_IDS", {42})
    monkeypatch.setattr(capture, "_grab_excluding", lambda ex: None)

    class FakeShot:
        size, bgra = (4, 2), b"\0" * 32

    class FakeMSS:
        monitors = [None, None]
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def grab(self, mon): return FakeShot()

    monkeypatch.setattr(capture.mss, "MSS", FakeMSS, raising=False)
    assert capture.grab().size == (4, 2)


def test_grab_paints_panel_gray_so_gemini_cannot_read_it(monkeypatch):
    from showme import capture

    monkeypatch.setattr(capture, "EXCLUDE_WINDOW_IDS", {42})
    monkeypatch.setattr(capture, "BLANK_WINDOW_IDS", {7})
    monkeypatch.setattr(capture, "_grab_excluding", lambda ex: Image.new("RGB", (100, 50), "white"))
    monkeypatch.setattr(capture, "_window_rects", lambda ids, w: [(60, 10, 30, 20)] if ids == {7} else [])
    img = capture.grab()
    assert img.getpixel((70, 20)) == capture.BLANK_COLOR  # 面板那块：纯灰
    assert img.getpixel((59, 20)) == (255, 255, 255) and img.getpixel((90, 20)) == (255, 255, 255)  # 其他地方不动
