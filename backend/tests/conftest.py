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


@pytest.fixture(autouse=True)
def _no_token_by_default(monkeypatch):
    """.env 里的 SHOWME_TOKEN 不影响测试；要测口令的用例自己 setenv。"""
    monkeypatch.delenv("SHOWME_TOKEN", raising=False)
