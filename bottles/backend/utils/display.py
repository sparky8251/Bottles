import os
import socket
from functools import lru_cache


# Every X server, whatever its implementation, listens on a socket named
# X<display> in this directory. Tests point it elsewhere.
X11_SOCKET_DIR = "/tmp/.X11-unix"


class DisplayUtils:
    @staticmethod
    @lru_cache
    def get_x_display():
        """Get the X display port."""
        env_var = "DISPLAY"
        ports_range = range(3)

        if os.environ.get(env_var):
            return os.environ.get(env_var)

        # Without DISPLAY, probe the first few display numbers through their
        # sockets, as xdpyinfo did before; neither shipped artifact carries
        # that binary. Connecting, not just finding the file, keeps a stale
        # socket left by a crashed server from counting as a display.
        for i in ports_range:
            if DisplayUtils._server_answers(os.path.join(X11_SOCKET_DIR, f"X{i}")):
                return f":{i}"

        return False

    @staticmethod
    def _server_answers(socket_path: str) -> bool:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(socket_path)
            except OSError:
                return False
        return True

    @staticmethod
    def display_server_type():
        """Return the display server type"""
        session_type = os.environ.get("XDG_SESSION_TYPE")
        if session_type:
            return session_type.lower()
        return "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"
