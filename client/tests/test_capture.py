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
