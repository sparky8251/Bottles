# ruff: noqa: E402
import urllib.error

from gi.repository import Gio

Gio.resources_register(Gio.Resource.load("/app/share/bottles/bottles.gresource"))

from bottles.frontend.windows import installer as installer_module
from bottles.frontend.windows.installer import fetch_installer_icon


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    @staticmethod
    def read():
        return b"icon"


def test_installer_icon_is_fetched_through_the_shared_client(monkeypatch):
    seen = {}

    def fetch(url, *, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return b"icon"

    monkeypatch.setattr("bottles.frontend.windows.installer.fetch", fetch)

    assert fetch_installer_icon("https://example.com/icon.png") == b"icon"
    assert seen["url"] == "https://example.com/icon.png"
    assert seen["timeout"] == installer_module.ICON_TIMEOUT


def test_installer_icon_http_error_is_ignored(monkeypatch):
    def fetch(_url, *, timeout):
        raise installer_module.HttpError("boom")

    monkeypatch.setattr("bottles.frontend.windows.installer.fetch", fetch)

    assert fetch_installer_icon("https://example.com/icon.png") is None


def test_installer_icon_without_url_is_none():
    assert fetch_installer_icon(None) is None
