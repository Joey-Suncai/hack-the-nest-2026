from showme.narrator import estimate_seconds
from showme.wording import intro, outro, speakable


def test_intro_zh_with_and_without_count():
    assert intro("VS Code", "从 GitHub clone 一个 repo。") == "我来带你在 VS Code 里从 GitHub clone 一个 repo。跟着屏幕上的光圈点就行。"
    assert "一共大约 3 步" in intro("VS Code", "clone 一个 repo", n_steps=3)


def test_outro_counts_steps():
    s = outro("VS Code", "clone 一个 repo", 3, done_text="太棒了！")
    assert s.startswith("太棒了！") and "3 步" in s


def test_english():
    goal = "Clone the GitHub repo https://github.com/octocat/Hello-World"
    assert intro("VS Code", goal, "en").startswith("I'll walk you through it in VS Code: clone the GitHub repo")
    assert "3 steps" in outro("VS Code", goal, 3, "en")


def test_speakable_shortens_urls():
    assert speakable("Clone the GitHub repo https://github.com/octocat/Hello-World") == "Clone the GitHub repo Hello-World"
    assert speakable("Paste https://github.com/octocat/Hello-World.git, then press Enter.") == "Paste Hello-World.git, then press Enter."
    assert speakable("Open https://github.com/ now") == "Open github.com now"
    assert speakable("No links here") == "No links here"


def test_caption_duration_has_floor_and_grows():
    assert estimate_seconds("好") == 2.5
    assert estimate_seconds("字" * 40) > estimate_seconds("字" * 10)


def test_ui_text_defaults_to_english():
    from showme import i18n

    i18n.set_lang("en")
    assert i18n.t("looking") == "Looking at your screen…"
    assert i18n.DEFAULT_LANG == "en"
    i18n.set_lang("zh")
    assert i18n.t("looking") == "正在看你的屏幕…"
    i18n.set_lang("xx")  # 不认识的语言退回默认
    assert i18n.get_lang() == "en"
