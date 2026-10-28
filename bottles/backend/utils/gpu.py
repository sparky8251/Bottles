# gpu.py
#
# Copyright 2025 mirkobrombin <brombin94@gmail.com>
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
#

import os
from enum import Enum
from functools import lru_cache

from bottles.backend.logger import Logger
from bottles.backend.utils.nvidia import get_nvidia_dll_path
from bottles.backend.utils.vulkan import VulkanUtils

logging = Logger()

# Overridden by tests; the real file is kernel-provided and read-only.
PROC_MODULES = "/proc/modules"
SYSFS_PCI_DEVICES = "/sys/bus/pci/devices"

# The raw PCI ids behind what `lspci | grep` used to match: the class names
# "VGA compatible controller", "3D controller" and "Display controller", and
# the pci.ids vendor names for NVIDIA, AMD/ATI (0x1002, not AMD's chipset id
# 0x1022) and Intel. Reading sysfs needs neither lspci nor a pci.ids database.
DISPLAY_CLASSES = ("0x0300", "0x0302", "0x0380")
VENDOR_IDS = {"nvidia": "0x10de", "amd": "0x1002", "intel": "0x8086"}


class GPUVendors(Enum):
    AMD = "amd"
    NVIDIA = "nvidia"
    INTEL = "intel"


# noinspection PyTypeChecker
class GPUUtils:
    __vendors = {
        "nvidia": "NVIDIA Corporation",
        "amd": "Advanced Micro Devices, Inc.",
        "intel": "Intel Corporation",
    }

    def __init__(self):
        self.vk = VulkanUtils()

    @staticmethod
    def _display_vendors() -> set:
        """Vendors with at least one PCI display controller, read from sysfs."""
        found = set()
        try:
            devices = os.listdir(SYSFS_PCI_DEVICES)
        except OSError:
            return found
        for device in devices:
            base = os.path.join(SYSFS_PCI_DEVICES, device)
            try:
                with open(os.path.join(base, "class"), encoding="ascii") as f:
                    pci_class = f.read().strip()
                with open(os.path.join(base, "vendor"), encoding="ascii") as f:
                    vendor_id = f.read().strip()
            except OSError:
                continue
            if pci_class[:6] not in DISPLAY_CLASSES:
                continue
            for vendor, wanted in VENDOR_IDS.items():
                if vendor_id == wanted:
                    found.add(vendor)
        return found

    @staticmethod
    def assume_discrete(vendors: list):
        if "nvidia" in vendors and "amd" in vendors:
            return {"integrated": "amd", "discrete": "nvidia"}
        if "nvidia" in vendors and "intel" in vendors:
            return {"integrated": "intel", "discrete": "nvidia"}
        if "amd" in vendors and "intel" in vendors:
            return {"integrated": "intel", "discrete": "amd"}
        return {}

    @staticmethod
    def is_nouveau():
        # /proc/modules is the one loaded-module list visible from inside the
        # flatpak sandbox: /sys/module is not mounted there and lsmod is not
        # shipped by the runtime, so both earlier approaches never fired.
        try:
            with open(PROC_MODULES, encoding="utf-8") as modules:
                loaded = any(line.split(" ", 1)[0] == "nouveau" for line in modules)
        except OSError:
            return False
        if loaded:
            logging.warning("Nouveau driver detected, this may cause issues")
        return loaded

    def get_gpu(self):
        # checked in this order on purpose: callers take the first vendor
        checks = ("nvidia", "amd", "intel")
        gpus = {
            "nvidia": {
                "vendor": "nvidia",
                "envs": {
                    "__NV_PRIME_RENDER_OFFLOAD": "1",
                    "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                    "__VK_LAYER_NV_optimus": "NVIDIA_only",
                },
                "icd": self.vk.get_vk_icd("nvidia", as_string=True),
                "nvngx_path": get_nvidia_dll_path(),
            },
            "amd": {
                "vendor": "amd",
                "envs": {"DRI_PRIME": "1"},
                "icd": self.vk.get_vk_icd("amd", as_string=True),
            },
            "intel": {
                "vendor": "intel",
                "envs": {"DRI_PRIME": "1"},
                "icd": self.vk.get_vk_icd("intel", as_string=True),
            },
        }
        found = []
        result = {"vendors": {}, "prime": {"integrated": None, "discrete": None}}

        nouveau_icd = self.vk.get_vk_icd("nouveau", as_string=True)
        if self.is_nouveau() or (not gpus["nvidia"]["icd"] and nouveau_icd):
            gpus["nvidia"]["envs"] = {"DRI_PRIME": "1"}
            gpus["nvidia"]["icd"] = nouveau_icd

        present = self._display_vendors()
        for _check in checks:
            if _check in present:
                found.append(_check)
                result["vendors"][_check] = gpus[_check]

        if len(found) >= 2:
            _discrete = self.assume_discrete(found)
            if _discrete:
                _integrated = _discrete["integrated"]
                _discrete = _discrete["discrete"]
                result["prime"]["integrated"] = gpus[_integrated]
                result["prime"]["discrete"] = gpus[_discrete]

        return result

    @staticmethod
    @lru_cache
    def is_gpu(vendor: GPUVendors) -> bool:
        return vendor.value in GPUUtils._display_vendors()
