import base64
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _tiny_png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


TINY_PNG_B64 = _tiny_png_b64()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
