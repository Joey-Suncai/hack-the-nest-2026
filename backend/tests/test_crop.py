import io

from PIL import Image

from app import steps
from app.steps import NextStepIn, NextStepOut, crop_image, crop_region, map_box_from_crop
from conftest import TINY_PNG_B64


def test_crop_region_pads_and_clamps():
    assert crop_region([400, 400, 500, 500]) == [300, 300, 600, 600]
    assert crop_region([0, 0, 100, 100]) == [0, 0, 200, 200]


def test_crop_region_min_size_for_tiny_box():
    r = crop_region([495, 495, 505, 505])
    assert r[2] - r[0] >= 200 and r[3] - r[1] >= 200
    assert r[0] <= 495 and r[2] >= 505


def test_map_box_from_crop():
    assert map_box_from_crop([0, 0, 1000, 1000], [200, 300, 400, 500]) == [200, 300, 400, 500]
    assert map_box_from_crop([500, 500, 1000, 1000], [200, 300, 400, 500]) == [300, 400, 400, 500]


def test_crop_image_returns_upscaled_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (1000, 500), "white").save(buf, "PNG")
    out = crop_image(buf.getvalue(), [0, 0, 500, 500])  # 左上 500x250 px
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and max(img.size) >= 1000


def _small(box):
    return NextStepOut(status="next", previous_ok=None, instruction="点图表", target_label="图表",
                       box=box, expect=None)


def test_refine_replaces_small_box(monkeypatch):
    calls = []

    def fake(prompt, schema, files=None, **kw):
        calls.append(schema)
        if schema is NextStepOut:
            return _small([400, 400, 500, 500])
        return steps.RefineOut(found=True, box=[500, 500, 1000, 1000])

    monkeypatch.setattr(steps, "REFINE", True)
    monkeypatch.setattr(steps.gemini, "generate_json", fake)
    out = steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64))
    assert len(calls) == 2 and out.box == [450, 450, 600, 600]


def test_refine_failure_keeps_original(monkeypatch):
    def fake(prompt, schema, files=None, **kw):
        if schema is NextStepOut:
            return _small([400, 400, 500, 500])
        raise steps.gemini.GeminiError("boom")

    monkeypatch.setattr(steps, "REFINE", True)
    monkeypatch.setattr(steps.gemini, "generate_json", fake)
    out = steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64))
    assert out.box == [400, 400, 500, 500]


def test_big_box_skips_refine(monkeypatch):
    calls = []

    def fake(prompt, schema, files=None, **kw):
        calls.append(schema)
        return _small([100, 100, 600, 600])

    monkeypatch.setattr(steps, "REFINE", True)
    monkeypatch.setattr(steps.gemini, "generate_json", fake)
    steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64))
    assert len(calls) == 1


def test_refine_runs_on_retry_even_when_disabled(monkeypatch):
    calls = []

    def fake(prompt, schema, files=None, **kw):
        calls.append(schema)
        if schema is NextStepOut:
            return _small([400, 400, 500, 500])
        return steps.RefineOut(found=True, box=[500, 500, 1000, 1000])

    monkeypatch.setattr(steps, "REFINE", False)
    monkeypatch.setattr(steps, "REFINE_ON_RETRY", True)
    monkeypatch.setattr(steps.gemini, "generate_json", fake)
    failed = [steps.HistoryItem(instruction="点 25", ok=False)]
    out = steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, history=failed))
    assert len(calls) == 2 and out.box == [450, 450, 600, 600]
    calls.clear()
    ok = [steps.HistoryItem(instruction="点 25", ok=True)]
    steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, history=ok))
    assert len(calls) == 1  # 上一步成功：不精修


def test_refine_runs_when_user_note_present(monkeypatch):
    calls = []

    def fake(prompt, schema, files=None, **kw):
        calls.append(schema)
        if schema is NextStepOut:
            return _small([400, 400, 500, 500])
        return steps.RefineOut(found=True, box=[500, 500, 1000, 1000])

    monkeypatch.setattr(steps, "REFINE", False)
    monkeypatch.setattr(steps, "REFINE_ON_RETRY", True)
    monkeypatch.setattr(steps.gemini, "generate_json", fake)
    hist = [steps.HistoryItem(instruction="Click Chrome", ok=None)]
    steps.next_step(NextStepIn(app="a", goal="g", screenshot=TINY_PNG_B64, history=hist,
                               user_note="user says they clicked the wrong thing"))
    assert len(calls) == 2
