# The egress side of the oracle: fakes for every side-effect boundary the
# step handlers cross, recording each call with repr'd arguments so that a
# re-typed or re-rendered value (a tuple where a list was, a changed shell
# string) is visible. The same fakes are patched into the old managers at
# the pre-refactor commit (legacy_egress_capture.py) and into the new
# executors (test_egress.py); the recorded sequences must match.
#
# The fakes do nothing beyond what later steps need to see: downloads and
# cab extractions create empty files, Wine never runs. Directory creation
# runs for real inside a throwaway prefix so that exists() checks take the
# same branches on both sides. What this cannot see: failure branches that
# depend on a real archive, registry or Wine process.
import contextlib
import os
import shlex
from collections import defaultdict
from types import SimpleNamespace
from typing import Any


class Result:
    """A stand-in with the three attributes the handlers read."""

    def __init__(self, status=True, data=None, message=""):
        self.status, self.data, self.message = status, data, message

    @property
    def ok(self):
        return self.status

    @property
    def has_data(self):
        return self.data is not None


class Recorder:
    def __init__(self, prefix: str, temp: str):
        self.calls: list[dict] = []
        self.prefix = prefix
        self.temp = temp

    def normalise(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self.prefix, "<prefix>").replace(self.temp, "<temp>")
        if isinstance(value, dict):
            return {self.normalise(k): self.normalise(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.normalise(v) for v in value)
        if callable(value):
            return "<callable>"
        if hasattr(value, "__dataclass_fields__"):  # RegItem and friends
            return {name: self.normalise(getattr(value, name)) for name in value.__dataclass_fields__}
        return value

    def record(self, call: str, *args, **kwargs):
        self.calls.append(
            {
                "call": call,
                "args": repr(self.normalise(list(args))),
                "kwargs": repr(self.normalise(dict(sorted(kwargs.items())))),
            }
        )


def make_prefix(root: str) -> str:
    prefix = os.path.join(root, "prefix")
    for sub in ("drive_c/windows/system32", "drive_c/windows/syswow64", "drive_c/windows/Fonts", "dosdevices"):
        os.makedirs(os.path.join(prefix, sub), exist_ok=True)
    # with z: present the installer is run in place, never staged through a
    # random temporary directory
    link = os.path.join(prefix, "dosdevices", "z:")
    if not os.path.lexists(link):
        os.symlink("/", link)
    return prefix


class _Fakes:
    """Factories for every patched name, all writing to one recorder."""

    def __init__(self, rec: Recorder):
        self.rec = rec

    def download(self, *args, **kwargs):
        self.rec.record("download", *args, **kwargs)
        # umu checks that the downloaded file exists in temp
        file = kwargs.get("rename") or kwargs.get("file") or (args[2] if len(args) > 2 and args[2] else None) or (args[1] if len(args) > 1 else None)
        if isinstance(file, str) and file:
            path = os.path.join(self.rec.temp, file)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "a").close()
        return Result(True)

    def wine_executor(self):
        rec = self.rec

        class WineExecutor:
            def __init__(self, config, **kwargs):
                rec.record("WineExecutor", **kwargs)

            def run(self):
                rec.record("WineExecutor.run")
                return Result(True)

        return WineExecutor

    def wine_command(self):
        rec = self.rec

        class WineCommand:
            def __init__(self, config, **kwargs):
                rec.record("WineCommand", **kwargs)
                self.returncode = 0

            def run(self):
                rec.record("WineCommand.run")
                return Result(True, data="")

        return WineCommand

    def wine_dbg(self):
        rec = self.rec

        class WineDbg:
            def __init__(self, config):
                pass

            def wait_for_process(self, name):
                rec.record("WineDbg.wait_for_process", name)
                return True

        return WineDbg

    def wine_path(self):
        rec = self.rec

        class WinePath:
            def __init__(self, config):
                pass

            def to_windows(self, path, native=False):
                rec.record("WinePath.to_windows", path, native=native)
                return "Z:" + path.replace("/", "\\")

        return WinePath

    def cab_extract(self):
        rec = self.rec

        class CabExtract:
            def run(self, *args, **kwargs):
                rec.record("CabExtract.run", *args, **kwargs)
                # real cabextract creates the destination and the files named
                # in `files`; without them get_from_cab's rename stops early
                # and the comparison would end at that step
                destination = kwargs.get("destination") or (args[2] if len(args) > 2 else None)
                if isinstance(destination, str) and destination:
                    os.makedirs(destination, exist_ok=True)
                    for name in kwargs.get("files") or ():
                        open(os.path.join(destination, os.path.basename(name)), "a").close()
                return True

        return CabExtract

    def patoolib(self):
        rec = self.rec
        return SimpleNamespace(extract_archive=lambda *a, **k: rec.record("patoolib.extract_archive", *a, **k))

    def shutil(self):
        rec = self.rec

        class Shutil:
            SameFileError = type("SameFileError", (OSError,), {})

            def __getattr__(self, name):
                def op(*args, **kwargs):
                    rec.record(f"shutil.{name}", *args, **kwargs)

                return op

        return Shutil()

    def os_module(self):
        rec = self.rec
        real = os

        class Os:
            path = real.path
            getenv = staticmethod(real.getenv)
            sep = real.sep

            def makedirs(self, path, *args, **kwargs):
                rec.record("os.makedirs", path, *args, **kwargs)
                return real.makedirs(path, *args, **kwargs)

            def listdir(self, path):
                rec.record("os.listdir", path)
                return sorted(real.listdir(path))

            def __getattr__(self, name):
                if name in ("remove", "unlink", "replace", "rename", "chmod", "symlink"):

                    def op(*args, **kwargs):
                        rec.record(f"os.{name}", *args, **kwargs)

                    return op
                return getattr(real, name)

        return Os()

    def glob(self):
        rec = self.rec

        def fake_glob(pattern, *args, **kwargs):
            rec.record("glob", pattern)
            # one deterministic match per pattern, so the per-file branch runs
            return [pattern.replace("*", "fake-match")]

        return fake_glob

    def reg(self):
        rec = self.rec

        class Reg:
            def __init__(self, config):
                pass

            def add(self, **kwargs):
                rec.record("Reg.add", **kwargs)

            def bulk_add(self, regs):
                rec.record("Reg.bulk_add", regs)

            def import_bundle(self, bundle):
                rec.record("Reg.import_bundle", bundle)
                return Result(True)

        return Reg

    def regsvr32(self):
        rec = self.rec

        class Regsvr32:
            def __init__(self, config):
                pass

            def register(self, dll):
                rec.record("Regsvr32.register", dll)
                return Result(True)

        return Regsvr32

    def regkeys(self):
        rec = self.rec

        class RegKeys:
            def __init__(self, config):
                pass

            def lg_set_windows(self, version):
                rec.record("RegKeys.lg_set_windows", version)

            def set_app_default(self, version, executable):
                rec.record("RegKeys.set_app_default", version, executable)

        return RegKeys

    def uninstaller(self):
        rec = self.rec

        class Uninstaller:
            def __init__(self, config):
                pass

            def from_name(self, name):
                rec.record("Uninstaller.from_name", name)

        return Uninstaller

    def subprocess(self):
        rec = self.rec

        class Popen:
            def __init__(self, command, **kwargs):
                rec.record("subprocess.Popen", command, cwd=kwargs.get("cwd"), shell=kwargs.get("shell"))
                self.returncode = 0

            def communicate(self):
                return "", ""

        return SimpleNamespace(Popen=Popen, PIPE=-1)

    def config_manager(self):
        rec = self.rec

        class ConfigManager:
            def __init__(self, **kwargs):
                rec.record("ConfigManager", **kwargs)

            def del_key(self, key):
                rec.record("ConfigManager.del_key", key)

            def merge_dict(self, data):
                rec.record("ConfigManager.merge_dict", data)

        return ConfigManager

    def tempfile(self):
        rec = self.rec
        import tempfile as real

        class Named:
            def __init__(self, dir=None, delete=True, **kwargs):
                self.name = os.path.join(dir or rec.temp, "fake-tmpfile")
                open(self.name, "a").close()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return SimpleNamespace(NamedTemporaryFile=Named, TemporaryDirectory=real.TemporaryDirectory)


# names the handlers reach through their module namespace, and which fake
# replaces each; a module that lacks a name is left alone
PATCHES = {
    "WineExecutor": "wine_executor",
    "WineCommand": "wine_command",
    "WineDbg": "wine_dbg",
    "WinePath": "wine_path",
    "CabExtract": "cab_extract",
    "patoolib": "patoolib",
    "shutil": "shutil",
    "os": "os_module",
    "glob": "glob",
    "Reg": "reg",
    "Regsvr32": "regsvr32",
    "RegKeys": "regkeys",
    "Uninstaller": "uninstaller",
    "subprocess": "subprocess",
    "ConfigManager": "config_manager",
    "tempfile": "tempfile",
}


@contextlib.contextmanager
def fake_environment(modules, recorder: Recorder, paths_module):
    """Patch every boundary name found in `modules`; point Paths.temp at the
    recorder's temp dir. Yields a stub component manager whose download
    records and succeeds."""
    fakes = _Fakes(recorder)
    saved = []
    for module in modules:
        for name, factory in PATCHES.items():
            if hasattr(module, name):
                saved.append((module, name, getattr(module, name)))
                setattr(module, name, getattr(fakes, factory)())
    saved.append((paths_module.Paths, "temp", paths_module.Paths.temp))
    paths_module.Paths.temp = recorder.temp
    try:
        yield SimpleNamespace(
            component_manager=SimpleNamespace(download=fakes.download),
            supported_dependencies={},
            update_config=lambda *a, **k: None,
            versioning_manager=SimpleNamespace(create_state=lambda **k: None),
        )
    finally:
        for module, name, value in reversed(saved):
            setattr(module, name, value)


def local_resources():
    return defaultdict(lambda: "<local>/local-resource.exe")


def shell_words(command: str) -> list[str]:
    return shlex.split(command)
