"""Тесты нормализации SOCKS5 URL и аргументов yt-dlp."""

from types import SimpleNamespace

from app.proxy import proxy_url, ytdlp_proxy_args


def test_proxy_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.proxy.get_settings",
        lambda: SimpleNamespace(socks5_proxy=""),
    )
    assert proxy_url() is None
    assert ytdlp_proxy_args() == []


def test_proxy_adds_scheme(monkeypatch):
    monkeypatch.setattr(
        "app.proxy.get_settings",
        lambda: SimpleNamespace(socks5_proxy="1.2.3.4:1080"),
    )
    assert proxy_url() == "socks5h://1.2.3.4:1080"
    assert ytdlp_proxy_args() == ["--proxy", "socks5h://1.2.3.4:1080"]


def test_proxy_keeps_socks5h(monkeypatch):
    monkeypatch.setattr(
        "app.proxy.get_settings",
        lambda: SimpleNamespace(socks5_proxy="socks5h://user:pass@host:1080"),
    )
    assert proxy_url() == "socks5h://user:pass@host:1080"


def test_proxy_keeps_socks5(monkeypatch):
    monkeypatch.setattr(
        "app.proxy.get_settings",
        lambda: SimpleNamespace(socks5_proxy="socks5://host:1080"),
    )
    assert proxy_url() == "socks5://host:1080"
