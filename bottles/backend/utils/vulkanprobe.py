# vulkanprobe.py
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

"""Answer "is Vulkan usable here" by asking the loader directly.

Run as a script: exit 0 when an instance can be created and at least one
physical device enumerates, 1 otherwise. This is what `vulkaninfo --summary`
decided its exit status on, without needing Vulkan-Tools built and shipped.
It is run in a child process on purpose: a broken ICD may crash or hang, and
that must take down the probe, not Bottles.
"""

import ctypes
import sys

VK_SUCCESS = 0
VK_STRUCTURE_TYPE_APPLICATION_INFO = 0
VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1
VK_API_VERSION_1_0 = 1 << 22


class VkApplicationInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int),
        ("pNext", ctypes.c_void_p),
        ("pApplicationName", ctypes.c_char_p),
        ("applicationVersion", ctypes.c_uint32),
        ("pEngineName", ctypes.c_char_p),
        ("engineVersion", ctypes.c_uint32),
        ("apiVersion", ctypes.c_uint32),
    ]


class VkInstanceCreateInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.POINTER(VkApplicationInfo)),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.c_void_p),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.c_void_p),
    ]


def physical_device_count() -> int:
    """Create a bare instance and count its physical devices."""
    vk = ctypes.CDLL("libvulkan.so.1")
    app = VkApplicationInfo(
        sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
        pApplicationName=b"bottles",
        apiVersion=VK_API_VERSION_1_0,
    )
    info = VkInstanceCreateInfo(
        sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        pApplicationInfo=ctypes.pointer(app),
    )
    instance = ctypes.c_void_p()
    if vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(instance)) != VK_SUCCESS:
        return 0
    count = ctypes.c_uint32(0)
    vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None)
    vk.vkDestroyInstance(instance, None)
    return count.value


def main() -> int:
    try:
        return 0 if physical_device_count() > 0 else 1
    except (OSError, AttributeError):
        # no loader, or a loader too old to export the entry points
        return 1


if __name__ == "__main__":
    sys.exit(main())
