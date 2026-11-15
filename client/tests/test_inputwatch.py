from showme.inputwatch import TypingTracker, click_in_rect, is_typing_step, to_top_left


def test_bottom_left_to_top_left():
    assert to_top_left(100, 1000, 1117) == (100, 117)
    assert to_top_left(0, 0, 1117) == (0, 1117)


def test_click_inside_and_with_padding():
    rect = (100, 200, 50, 30)  # x, y, w, h
    assert click_in_rect((120, 210), rect, 0)
    assert click_in_rect((95, 205), rect, 20)  # 框左边 5 点，放宽 20 之内
    assert not click_in_rect((95, 205), rect, 0)


def test_click_outside():
    rect = (100, 200, 50, 30)
    assert not click_in_rect((300, 210), rect, 20)
    assert not click_in_rect((120, 280), rect, 20)
    assert not click_in_rect((120, 210), None, 20)


def test_typing_step_detection():
    assert is_typing_step("Type the song name and press Enter.")
    assert is_typing_step("Click the search box at the top.")
    assert is_typing_step("Paste the repository URL into the box at the top.")
    assert is_typing_step("在搜索框里输入歌名")
    assert is_typing_step("Click the box.", target_label="Search")
    assert not is_typing_step("Click Clone Git Repository under Start.")
    assert not is_typing_step("Click the blue Save button.", expect="The meeting is saved.")


def test_typing_tracker_enter_and_idle():
    t = TypingTracker(1.2)
    assert t.done(now=0) is None
    t.key(False, now=10.0)
    assert t.done(now=10.5) is None
    assert t.done(now=11.3) == "idle"
    t.reset()
    t.key(True, now=20.0)
    assert t.done(now=20.0) == "enter"


def test_look_at_screen_offers_step_by_step_only_for_how_to():
    from showme.controller import with_offer

    out = with_offer("Use File > Save As.", "How can I change an R Markdown file to an R file?")
    assert out.startswith("Use File > Save As.") and "step by step" in out and "show_next_step" in out
    assert "How can I change an R Markdown file" in out  # goal 给 Agent
    assert with_offer("Clone copies a repo to your computer.", "What does clone mean?") == "Clone copies a repo to your computer."
    assert "step by step" in with_offer("…", "Where do I turn on dark mode?")
