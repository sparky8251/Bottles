# icon.py
#
# Copyright 2026 Bottles Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, in version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ICON_SIZE = 256


def convert_to_png(source: str, destination: str, size: int = ICON_SIZE) -> None:
    """Write the best frame of an icon file as a PNG fitting within size x size.

    GdkPixbuf's ICO loader takes the frame matching the requested size when
    there is one and the largest otherwise, then scales to fit, upscaling
    smaller icons; that is what the previous `magick -thumbnail 256x256`
    pipeline produced, without a built ImageMagick. Load errors raise
    GLib.Error for the caller to handle.
    """
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(source, size, size)
    pixbuf.savev(destination, "png", [], [])
