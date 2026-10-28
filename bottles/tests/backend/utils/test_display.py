import pytest

from bottles.backend.utils import display as display_module
from bottles.backend.utils.display import DisplayUtils


@pytest.mark.parametrize(
    "session_type,wayland_display,expected",
    [
        (None, "wayland-0", "wayland"),
        ("", "wayland-0", "wayland"),
        (None, None, "x11"),
        (None, "", "x11"),
        ("X11", "wayland-0", "x11"),
        ("WAYLAND", None, "wayland"),
    ],
)
def test_display_server_type(monkeypatch, session_type, wayland_display, expected):
    for name, value in (
        ("XDG_SESSION_TYPE", session_type),
        ("WAYLAND_DISPLAY", wayland_display),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    assert DisplayUtils.display_server_type() == expected


def test_get_x_display_prefers_environment(monkeypatch):
    DisplayUtils.get_x_display.cache_clear()
    monkeypatch.setenv("DISPLAY", ":7")
    assert DisplayUtils.get_x_display() == ":7"
    DisplayUtils.get_x_display.cache_clear()


def test_get_x_display_falls_back_to_an_answering_server_socket(monkeypatch, tmp_path):
    import socket

    DisplayUtils.get_x_display.cache_clear()
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(display_module, "X11_SOCKET_DIR", str(tmp_path))

    assert DisplayUtils.get_x_display() is False

    # a socket file nobody listens on, as left behind by a crashed server
    DisplayUtils.get_x_display.cache_clear()
    (tmp_path / "X0").touch()
    assert DisplayUtils.get_x_display() is False

    DisplayUtils.get_x_display.cache_clear()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(tmp_path / "X1"))
        server.listen(1)
        assert DisplayUtils.get_x_display() == ":1"
    DisplayUtils.get_x_display.cache_clear()
