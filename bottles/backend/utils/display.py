import os
import subprocess
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

        # Without DISPLAY, probe the first few display numbers by their
        # sockets. Asking xdpyinfo only recognised servers that print "x.org",
        # which Xwayland and other servers do not, and needed a binary neither
        # shipped artifact carries.
        for i in ports_range:
            if os.path.exists(os.path.join(X11_SOCKET_DIR, f"X{i}")):
                return f":{i}"

        return False

    @staticmethod
    def check_nvidia_device():
        """Check if there is an nvidia device connected"""
        _query = "NVIDIA Corporation".lower()
        _proc = (
            subprocess.Popen(
                "lspci | grep 'VGA'",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
            )
            .communicate()[0]
            .decode("utf-8")
            .lower()
        )

        if _query in _proc:
            return True
        return False

    @staticmethod
    def display_server_type():
        """Return the display server type"""
        session_type = os.environ.get("XDG_SESSION_TYPE")
        if session_type:
            return session_type.lower()
        return "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"
