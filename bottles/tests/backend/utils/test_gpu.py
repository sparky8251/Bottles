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

    def fail(*_args, **_kwargs):
        raise AssertionError("no subprocess should be needed")

    monkeypatch.setattr(gpu_module.subprocess, "Popen", fail)

    assert GPUUtils.is_nouveau() is True


def test_nouveau_detection_ignores_prefix_matches_and_missing_file(monkeypatch, tmp_path):
    modules = tmp_path / "modules"
    modules.write_text("nouveau_extra 1 0 - Live 0x0\n")
    monkeypatch.setattr(gpu_module, "PROC_MODULES", str(modules))
    assert GPUUtils.is_nouveau() is False

    monkeypatch.setattr(gpu_module, "PROC_MODULES", str(tmp_path / "absent"))
    assert GPUUtils.is_nouveau() is False


def test_nouveau_gpu_uses_nvk_icd(monkeypatch):
    class FakeProcess:
        def __init__(self, command, **_kwargs):
            self.command = command

        def communicate(self):
            if "NVIDIA" in self.command:
                return b"01:00.0 VGA compatible controller: NVIDIA Corporation", b""
            return b"", b""

    monkeypatch.setattr(gpu_module.subprocess, "Popen", FakeProcess)
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


def test_nvidia_gpu_falls_back_to_available_nvk_icd(monkeypatch):
    class FakeProcess:
        def __init__(self, command, **_kwargs):
            self.command = command

        def communicate(self):
            if "NVIDIA" in self.command:
                return b"01:00.0 VGA compatible controller: NVIDIA Corporation", b""
            return b"", b""

    monkeypatch.setattr(gpu_module.subprocess, "Popen", FakeProcess)
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
