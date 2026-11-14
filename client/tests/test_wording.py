from showme.narrator import estimate_seconds
from showme.wording import intro, outro


def test_intro_zh_with_and_without_count():
    assert intro("Numbers", "把数据做成柱状图。") == "我来带你在 Numbers 里把数据做成柱状图。跟着屏幕上的光圈点就行。"
    assert "一共大约 3 步" in intro("Numbers", "做图", n_steps=3)


def test_outro_counts_steps():
    s = outro("Numbers", "做柱状图", 3, done_text="太棒了！")
    assert s.startswith("太棒了！") and "3 步" in s


def test_english():
    assert intro("Numbers", "make a chart", "en").startswith("I'll walk you through it in Numbers")
    assert "3 steps" in outro("Numbers", "make a chart", 3, "en")


def test_caption_duration_has_floor_and_grows():
    assert estimate_seconds("好") == 2.5
    assert estimate_seconds("字" * 40) > estimate_seconds("字" * 10)
