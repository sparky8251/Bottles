import struct

import gi
import pytest

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from bottles.backend.utils.icon import convert_to_png  # noqa: E402


def _png_frame(size: int, rgba: int) -> bytes:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    pixbuf.fill(rgba)
    return bytes(pixbuf.save_to_bufferv("png", [], [])[1])


def _write_ico(path, frames):
    # ICONDIR + ICONDIRENTRY per frame, PNG-compressed payloads
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b""
    payload = b""
    for size, data in frames:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    path.write_bytes(header + entries + payload)


def _corner_colour(path) -> int:
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
    return int.from_bytes(bytes(pixbuf.get_pixels()[0:3]), "big")


def test_convert_picks_the_256_frame_and_keeps_alpha(tmp_path):
    ico = tmp_path / "multi.ico"
    _write_ico(ico, [(16, _png_frame(16, 0xFF0000FF)), (256, _png_frame(256, 0x0000FF80))])
    out = tmp_path / "multi.png"

    convert_to_png(str(ico), str(out))

    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(out))
    assert (pixbuf.get_width(), pixbuf.get_height()) == (256, 256)
    assert pixbuf.get_has_alpha()
    assert _corner_colour(out) == 0x0000FF


def test_convert_upscales_a_small_only_icon_from_its_largest_frame(tmp_path):
    ico = tmp_path / "small.ico"
    _write_ico(ico, [(16, _png_frame(16, 0xFF0000FF)), (48, _png_frame(48, 0x00FF00FF))])
    out = tmp_path / "small.png"

    convert_to_png(str(ico), str(out))

    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(out))
    assert (pixbuf.get_width(), pixbuf.get_height()) == (256, 256)
    assert _corner_colour(out) == 0x00FF00


def test_convert_raises_on_an_unreadable_source(tmp_path):
    bad = tmp_path / "bad.ico"
    bad.write_bytes(b"not an icon")
    with pytest.raises(GLib.Error):
        convert_to_png(str(bad), str(tmp_path / "bad.png"))
