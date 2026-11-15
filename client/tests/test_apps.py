import os

from showme import apps


def _fake(monkeypatch, name, pid):
    monkeypatch.setattr(apps, "_read", lambda: (name, pid))


def test_returns_last_app_when_showme_is_frontmost(monkeypatch):
    monkeypatch.setattr(apps, "_last", None)
    _fake(monkeypatch, "Finder", 123)
    assert apps.frontmost_app() == "Finder"
    _fake(monkeypatch, "Python", os.getpid())  # 用户点了 ShowMe 面板
    assert apps.frontmost_app() == "Finder"
    _fake(monkeypatch, "ShowMe", 456)
    assert apps.frontmost_app() == "Finder"


def test_none_before_any_other_app_seen(monkeypatch):
    monkeypatch.setattr(apps, "_last", None)
    _fake(monkeypatch, "Python", os.getpid())
    assert apps.frontmost_app() is None


def test_error_returns_none(monkeypatch):
    def boom():
        raise RuntimeError("no AppKit")

    monkeypatch.setattr(apps, "_last", "Finder")
    monkeypatch.setattr(apps, "_read", boom)
    assert apps.frontmost_app() is None


def test_real_call_does_not_crash():
    name = apps.frontmost_app()
    assert name is None or isinstance(name, str)
