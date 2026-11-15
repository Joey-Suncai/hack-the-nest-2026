from showme.hotkey import (
    DEVICE_LEFT_OPTION,
    DEVICE_RIGHT_OPTION,
    FLAG_COMMAND,
    FLAG_OPTION,
    FLAG_SHIFT,
    RIGHT_OPTION_KEYCODE,
    RightOptionTap,
)

DOWN = FLAG_OPTION | DEVICE_RIGHT_OPTION
UP = 0


def test_clean_tap_toggles_once_on_release():
    t = RightOptionTap()
    assert t.flags_changed(RIGHT_OPTION_KEYCODE, DOWN) is False  # 按下不切换
    assert t.flags_changed(RIGHT_OPTION_KEYCODE, UP) is True  # 松开才切换
    assert t.flags_changed(RIGHT_OPTION_KEYCODE, UP) is False  # 重复的松开事件不再切换


def test_typing_while_held_does_not_toggle():
    t = RightOptionTap()
    t.flags_changed(RIGHT_OPTION_KEYCODE, DOWN)
    t.key_down()  # Option+e 打特殊符号
    assert t.flags_changed(RIGHT_OPTION_KEYCODE, UP) is False


def test_other_modifier_while_held_does_not_toggle():
    t = RightOptionTap()
    t.flags_changed(RIGHT_OPTION_KEYCODE, DOWN)
    t.flags_changed(56, DOWN | FLAG_SHIFT)  # 又按了 Shift
    assert t.flags_changed(RIGHT_OPTION_KEYCODE, UP) is False


def test_pressed_together_with_command_does_not_toggle():
    t = RightOptionTap()
    t.flags_changed(RIGHT_OPTION_KEYCODE, DOWN | FLAG_COMMAND)
    assert t.flags_changed(RIGHT_OPTION_KEYCODE, FLAG_COMMAND) is False


def test_left_option_is_ignored():
    t = RightOptionTap()
    assert t.flags_changed(58, FLAG_OPTION | DEVICE_LEFT_OPTION) is False
    assert t.flags_changed(58, UP) is False


def test_next_clean_tap_works_after_a_dirty_one():
    t = RightOptionTap()
    t.flags_changed(RIGHT_OPTION_KEYCODE, DOWN)
    t.key_down()
    t.flags_changed(RIGHT_OPTION_KEYCODE, UP)
    t.flags_changed(RIGHT_OPTION_KEYCODE, DOWN)
    assert t.flags_changed(RIGHT_OPTION_KEYCODE, UP) is True
