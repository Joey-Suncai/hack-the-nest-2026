"""bench/run.py 的纯逻辑：命中 / loop / next_guess 匹配 / history+expect 串联 / 跳过没拍的步。不联网。"""
import json

import pytest
from PIL import Image

from app import steps
from app.steps import NextGuess, NextStepOut
from bench import label, run


def _out(status="next", box=None, label_=None, guess=None, instruction="Click it.", expect=None):
    return NextStepOut(status=status, previous_ok=None, instruction=instruction, target_label=label_,
                       box=box, expect=expect,
                       next_guess=NextGuess(label=guess, instruction="Then click that.") if guess else None)


@pytest.fixture
def shots(tmp_path):
    """3 张 100x100 截图，返回目录。"""
    for i in (1, 2, 3):
        Image.new("RGB", (100, 100), "white").save(tmp_path / f"s{i}.png")
    return tmp_path


def _task(steps_):
    return {"id": "t", "app": "App", "goal": "Do the thing", "lang": "en", "frontmost_app": "App", "steps": steps_}


def test_labels_match_is_case_sensitive_substring():
    assert run.labels_match("Compose", "Compose")
    assert run.labels_match("Send", "Send now")
    assert run.labels_match("Clone from URL ...", "Clone from URL")
    assert not run.labels_match("send", "Send")
    assert not run.labels_match(None, "Send")
    assert not run.labels_match("Send", None)


def test_same_label_for_loop():
    assert run.same_label("Compose ", "compose")
    assert not run.same_label(None, None)
    assert not run.same_label("To", "Subject")


def test_score_box_center_and_status():
    step = {"target_px": [10, 10, 50, 50]}  # 100x100 图 → 归一化 [100,100,500,500]
    assert run.score(step, _out(box=[200, 200, 400, 400]), (100, 100))[0]
    assert not run.score(step, _out(box=[600, 600, 900, 900]), (100, 100))[0]
    assert not run.score(step, _out(status="unsure"), (100, 100))[0]
    assert run.score({**step, "accept_unsure": True}, _out(status="unsure"), (100, 100))[0]
    assert run.score({"expect_status": "done"}, _out(status="done"), (100, 100))[0]
    assert not run.score({"expect_status": "done"}, _out(box=[1, 1, 9, 9]), (100, 100))[0]
    assert run.score({"expect_status": "unsure"}, _out(status="unsure"), (100, 100))[0]
    assert not run.score(step, None, (100, 100))[0]


def test_chaining_history_expect_loop_and_guess(shots, monkeypatch):
    task = _task([
        {"file": "s1.png", "target_px": [10, 10, 50, 50]},
        {"file": "s2.png", "target_px": [10, 10, 50, 50], "frontmost_app": "Other"},
        {"file": "s3.png", "target_px": None, "expect_status": "done"},
    ])
    outs = [
        _out(box=[200, 200, 400, 400], label_="Compose", guess="To", instruction="Click Compose.", expect="E1"),
        _out(box=[800, 800, 900, 900], label_="compose", guess="Subj", instruction="Click Compose again.", expect="E2"),
        _out(status="done", instruction="All done!"),
    ]
    seen = []

    def fake(body):
        seen.append(body)
        return outs[len(seen) - 1]

    monkeypatch.setattr(steps, "next_step", fake)
    t = run.run_task(task, base=shots)

    assert [b.expect for b in seen] == [None, "E1", "E2"]
    assert [len(b.history) for b in seen] == [0, 1, 2]
    assert seen[1].history[0].instruction == "Click Compose." and seen[1].history[0].ok is True
    assert seen[2].history[1].ok is False  # 第 2 步没命中 → ok=false
    assert [b.frontmost_app for b in seen] == ["App", "Other", "App"]
    assert seen[0].lang == "en" and seen[0].goal == "Do the thing"

    r1, r2, r3 = t["steps"]
    assert (r1["hit"], r2["hit"], r3["hit"]) == (True, False, True)
    assert r2["loop"] is True and r1["loop"] is False
    assert r1["guess_hit"] is False  # "To" vs "compose"
    assert r2["guess_hit"] is False  # 下一步 done，没 target_label
    assert t["hits"] == 2 and t["success"] is False and t["loops"] == 1
    assert t["guess_given"] == 2 and t["guess_checked"] == 2 and t["guess_hit"] == 0


def test_guess_hit_counts(shots, monkeypatch):
    task = _task([{"file": "s1.png", "target_px": [10, 10, 50, 50]},
                  {"file": "s2.png", "target_px": [10, 10, 50, 50]}])
    outs = iter([_out(box=[200, 200, 400, 400], label_="Compose", guess="To"),
                 _out(box=[200, 200, 400, 400], label_="To recipients")])
    monkeypatch.setattr(steps, "next_step", lambda body: next(outs))
    t = run.run_task(task, base=shots)
    assert t["steps"][0]["guess_hit"] is True
    assert t["success"] is True and t["guess_hit"] == 1


def test_missing_and_unlabeled_steps_are_skipped(shots, monkeypatch):
    task = _task([
        {"file": "s1.png", "target_px": [10, 10, 50, 50]},
        {"file": None, "target_px": None},
        {"file": "nope.png", "target_px": [1, 1, 2, 2]},
        {"file": "s3.png", "target_px": None},  # 拍了没标
    ])
    calls = []
    monkeypatch.setattr(steps, "next_step",
                        lambda body: calls.append(body) or _out(box=[200, 200, 400, 400], label_="A", guess="B"))
    t = run.run_task(task, base=shots)
    assert len(calls) == 1
    assert [r.get("skipped") for r in t["steps"]] == [None, "not shot yet", "not shot yet", "not labeled yet"]
    assert t["n_ran"] == 1 and t["n_skipped"] == 3
    assert t["success"] is None  # 全对但不完整
    assert "guess_hit" not in t["steps"][0]  # 下一步没跑，无法核对


def test_error_does_not_crash(shots, monkeypatch):
    task = _task([{"file": "s1.png", "target_px": [10, 10, 50, 50]},
                  {"file": "s2.png", "target_px": [10, 10, 50, 50]}])
    seen = []

    def fake(body):
        seen.append(body)
        if len(seen) == 1:
            raise steps.gemini.GeminiError("boom", 502)
        return _out(box=[200, 200, 400, 400])

    monkeypatch.setattr(steps, "next_step", fake)
    t = run.run_task(task, base=shots)
    assert t["steps"][0]["error"] == "boom" and not t["steps"][0]["hit"]
    assert seen[1].history == [] and seen[1].expect is None


def test_aggregate_runs():
    def summ(success, hits, n, skipped=0):
        return {"id": "t", "steps": [{"hit": True, "latency_s": 1.0}] * n, "n_ran": n, "n_skipped": skipped,
                "hits": hits, "success": success, "total_latency_s": float(n), "mean_latency_s": 1.0, "loops": 0,
                "guess_given": 0, "guess_checked": 0, "guess_hit": 0}
    agg = run.aggregate([[summ(True, 3, 3)], [summ(False, 2, 3)]])
    assert agg["tasks"]["t"]["success"] == 1
    assert agg["task_success_mean"] == 0.5 and agg["task_success_min"] == 0 and agg["task_success_max"] == 1
    assert agg["tasks"]["t"]["step_hit_min"] == pytest.approx(2 / 3)


def test_tasks_json_is_valid():
    tasks = run.load_tasks()
    ids = [t["id"] for t in tasks]
    assert ids == ["simple_text_size", "simple_gmail", "complex_zoom_invite", "vscode_clone_demo"]
    for t in tasks:
        assert t["lang"] == "en" and t["goal"] and t["steps"]
        for s in t["steps"]:
            assert s["state"] and s["target_hint"]
            assert s.get("expect_status") in (None, "done", "unsure")
    demo = tasks[-1]
    assert all(run.skip_reason(s) is None for s in demo["steps"])  # 已有截图，可直接跑


def test_label_update_step():
    tasks = json.loads(json.dumps(run.load_tasks()))
    s = label.update_step(tasks, "simple_gmail", 2, "shots/simple_gmail__02.jpg", [1, 2, 3, 4], None)
    assert s["file"] == "shots/simple_gmail__02.jpg" and s["target_px"] == [1, 2, 3, 4]
    s = label.update_step(tasks, "simple_text_size", 4, "shots/x.jpg", None, "done")
    assert s["expect_status"] == "done" and s["target_px"] is None
    with pytest.raises(SystemExit):
        label.update_step(tasks, "simple_gmail", 9, "x", None, "done")
    with pytest.raises(SystemExit):
        label.update_step(tasks, "nope", 1, "x", None, "done")
    assert label.shot_name("simple_gmail", 3) == "simple_gmail__03"
