import subprocess

import pytest

from bottles.backend.utils import vulkan as vulkan_module
from bottles.backend.utils.vulkan import VulkanUtils


@pytest.fixture(autouse=True)
def _fresh_cache():
    VulkanUtils.check_support.cache_clear()
    yield
    VulkanUtils.check_support.cache_clear()


def _fake_run(returncode=None, raise_exc=None, seen=None):
    def run(argv, **kwargs):
        if seen is not None:
            seen.append((argv, kwargs))
        if raise_exc is not None:
            raise raise_exc
        return subprocess.CompletedProcess(argv, returncode)

    return run


def test_check_support_true_when_probe_finds_a_device(monkeypatch):
    seen = []
    monkeypatch.setattr(vulkan_module.subprocess, "run", _fake_run(0, seen=seen))
    assert VulkanUtils.check_support() is True
    argv, kwargs = seen[0]
    assert argv[0] == vulkan_module.sys.executable
    assert argv[1] == "-I"
    assert argv[2].endswith("vulkanprobe.py")
    assert kwargs["timeout"] == 10


def test_check_support_false_when_probe_finds_none(monkeypatch):
    monkeypatch.setattr(vulkan_module.subprocess, "run", _fake_run(1))
    assert VulkanUtils.check_support() is False


@pytest.mark.parametrize(
    "exc",
    [subprocess.TimeoutExpired(cmd="probe", timeout=10), OSError("no interpreter")],
)
def test_check_support_false_when_probe_cannot_run(monkeypatch, exc):
    monkeypatch.setattr(vulkan_module.subprocess, "run", _fake_run(raise_exc=exc))
    assert VulkanUtils.check_support() is False


def test_check_support_is_cached(monkeypatch):
    seen = []
    monkeypatch.setattr(vulkan_module.subprocess, "run", _fake_run(0, seen=seen))
    assert VulkanUtils.check_support() is True
    assert VulkanUtils.check_support() is True
    assert len(seen) == 1
