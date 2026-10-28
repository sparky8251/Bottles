from bottles.backend.utils import gpu as gpu_module
from bottles.backend.utils.gpu import GPUUtils
from bottles.backend.utils.vulkan import VulkanUtils


def test_vulkan_detects_nouveau_icds(monkeypatch, tmp_path):
    vulkan_dir = tmp_path / "vulkan"
    icd_dir = vulkan_dir / "icd.d"
    icd_dir.mkdir(parents=True)
    x86_64 = icd_dir / "nouveau_icd.x86_64.json"
    i686 = icd_dir / "nouveau_icd.i686.json"
    nvk = icd_dir / "nvk_icd.json"
    x86_64.touch()
    i686.touch()
    nvk.touch()

    monkeypatch.setattr(
        VulkanUtils,
        "_VulkanUtils__vk_icd_dirs",
        [str(vulkan_dir)],
    )

    assert set(VulkanUtils().get_vk_icd("nouveau")) == {
        str(x86_64),
        str(i686),
        str(nvk),
    }


def test_nouveau_detection_reads_proc_modules(monkeypatch, tmp_path):
    modules = tmp_path / "modules"
    modules.write_text("amdgpu 1 0 - Live 0x0\nnouveau 2 0 - Live 0x0\n")
    monkeypatch.setattr(gpu_module, "PROC_MODULES", str(modules))
    monkeypatch.setattr(gpu_module, "SYSFS_MODULE_NOUVEAU", str(tmp_path / "no-sysfs"))

    assert GPUUtils.is_nouveau() is True


def test_nouveau_detection_sees_a_built_in_driver_through_sysfs(monkeypatch, tmp_path):
    (tmp_path / "nouveau").mkdir()
    monkeypatch.setattr(gpu_module, "SYSFS_MODULE_NOUVEAU", str(tmp_path / "nouveau"))
    monkeypatch.setattr(gpu_module, "PROC_MODULES", str(tmp_path / "no-proc"))
    assert GPUUtils.is_nouveau() is True


def test_nouveau_detection_ignores_prefix_matches_and_missing_file(monkeypatch, tmp_path):
    modules = tmp_path / "modules"
    modules.write_text("nouveau_extra 1 0 - Live 0x0\n")
    monkeypatch.setattr(gpu_module, "PROC_MODULES", str(modules))
    monkeypatch.setattr(gpu_module, "SYSFS_MODULE_NOUVEAU", str(tmp_path / "no-sysfs"))
    assert GPUUtils.is_nouveau() is False

    monkeypatch.setattr(gpu_module, "PROC_MODULES", str(tmp_path / "absent"))
    assert GPUUtils.is_nouveau() is False


def _fake_sysfs(tmp_path, devices):
    """Build /sys/bus/pci/devices look-alikes: {address: (class, vendor)}."""
    root = tmp_path / "pci"
    for address, (pci_class, vendor) in devices.items():
        d = root / address
        d.mkdir(parents=True)
        (d / "class").write_text(pci_class + "\n")
        (d / "vendor").write_text(vendor + "\n")
    return str(root)


def test_display_vendors_filters_on_class_and_vendor_id(monkeypatch, tmp_path):
    root = _fake_sysfs(
        tmp_path,
        {
            "0000:03:00.0": ("0x030000", "0x1002"),  # AMD VGA controller
            "0000:01:00.0": ("0x030200", "0x10de"),  # NVIDIA as a 3D controller (Optimus)
            "0000:00:02.0": ("0x038000", "0x8086"),  # Intel as a Display controller
            "0000:00:00.0": ("0x060000", "0x1022"),  # AMD host bridge: AMD, not a GPU
            "0000:05:00.0": ("0x020000", "0x8086"),  # Intel network card: not a GPU
            "0000:06:00.0": ("0x030000", "0x1af4"),  # virtio GPU: display, unknown vendor
        },
    )
    monkeypatch.setattr(gpu_module, "SYSFS_PCI_DEVICES", root)

    assert GPUUtils._display_vendors() == {"amd", "nvidia", "intel"}


def test_display_vendors_ignores_unreadable_entries_and_missing_sysfs(monkeypatch, tmp_path):
    root = _fake_sysfs(tmp_path, {"0000:03:00.0": ("0x030000", "0x1002")})
    (tmp_path / "pci" / "0000:04:00.0").mkdir()  # no class/vendor files
    monkeypatch.setattr(gpu_module, "SYSFS_PCI_DEVICES", root)
    assert GPUUtils._display_vendors() == {"amd"}

    monkeypatch.setattr(gpu_module, "SYSFS_PCI_DEVICES", str(tmp_path / "absent"))
    assert GPUUtils._display_vendors() == set()


def test_get_gpu_lists_vendors_in_fixed_order_and_assigns_prime(monkeypatch, tmp_path):
    root = _fake_sysfs(
        tmp_path,
        {
            "0000:00:02.0": ("0x030000", "0x8086"),
            "0000:01:00.0": ("0x030200", "0x10de"),
        },
    )
    monkeypatch.setattr(gpu_module, "SYSFS_PCI_DEVICES", root)
    monkeypatch.setattr(GPUUtils, "is_nouveau", lambda _self: False)
    monkeypatch.setattr(gpu_module, "get_nvidia_dll_path", lambda: None)

    gpu = GPUUtils()
    monkeypatch.setattr(gpu.vk, "get_vk_icd", lambda vendor, as_string=False: f"/{vendor}.json")

    result = gpu.get_gpu()

    assert list(result["vendors"]) == ["nvidia", "intel"]
    assert result["prime"]["discrete"]["vendor"] == "nvidia"
    assert result["prime"]["integrated"]["vendor"] == "intel"


def test_is_gpu_reports_display_controllers_only(monkeypatch, tmp_path):
    root = _fake_sysfs(
        tmp_path,
        {
            "0000:00:00.0": ("0x060000", "0x10de"),  # an NVIDIA bridge, not a GPU
        },
    )
    monkeypatch.setattr(gpu_module, "SYSFS_PCI_DEVICES", root)
    GPUUtils.is_gpu.cache_clear()
    assert GPUUtils.is_gpu(gpu_module.GPUVendors.NVIDIA) is False
    GPUUtils.is_gpu.cache_clear()

    (tmp_path / "pci" / "0000:01:00.0").mkdir()
    (tmp_path / "pci" / "0000:01:00.0" / "class").write_text("0x030000\n")
    (tmp_path / "pci" / "0000:01:00.0" / "vendor").write_text("0x10de\n")
    assert GPUUtils.is_gpu(gpu_module.GPUVendors.NVIDIA) is True
    GPUUtils.is_gpu.cache_clear()


def test_nouveau_gpu_uses_nvk_icd(monkeypatch, tmp_path):
    root = _fake_sysfs(tmp_path, {"0000:01:00.0": ("0x030000", "0x10de")})
    monkeypatch.setattr(gpu_module, "SYSFS_PCI_DEVICES", root)
    monkeypatch.setattr(GPUUtils, "is_nouveau", lambda _self: True)
    monkeypatch.setattr(gpu_module, "get_nvidia_dll_path", lambda: None)

    gpu = GPUUtils()
    monkeypatch.setattr(
        gpu.vk,
        "get_vk_icd",
        lambda vendor, as_string=False: f"/{vendor}.json",
    )

    nvidia = gpu.get_gpu()["vendors"]["nvidia"]

    assert nvidia["envs"] == {"DRI_PRIME": "1"}
    assert nvidia["icd"] == "/nouveau.json"


def test_nvidia_gpu_falls_back_to_available_nvk_icd(monkeypatch, tmp_path):
    root = _fake_sysfs(tmp_path, {"0000:01:00.0": ("0x030000", "0x10de")})
    monkeypatch.setattr(gpu_module, "SYSFS_PCI_DEVICES", root)
    monkeypatch.setattr(GPUUtils, "is_nouveau", lambda _self: False)
    monkeypatch.setattr(gpu_module, "get_nvidia_dll_path", lambda: None)

    gpu = GPUUtils()
    monkeypatch.setattr(
        gpu.vk,
        "get_vk_icd",
        lambda vendor, as_string=False: (
            "/nouveau_icd.x86_64.json" if vendor == "nouveau" else ""
        ),
    )

    nvidia = gpu.get_gpu()["vendors"]["nvidia"]

    assert nvidia["envs"] == {"DRI_PRIME": "1"}
    assert nvidia["icd"] == "/nouveau_icd.x86_64.json"
