# Capture golden traces from the manifest interpreters as they were before the
# steps package existed. Run once, from the repo root, at that commit:
#
#   python -I bottles/tests/backend/steps/legacy_capture.py CORPUS_DIR GOLDEN_DIR
#
# It drives DependencyManager.install, InstallerManager.__perform_steps and
# UmuDependencyInstaller._plan over every corpus manifest with the handlers
# replaced by recorders, and writes one JSON line per (manifest, arch). The
# steps package's trace tests compare against these files, so the lines are
# the behaviour contract: which handler ran, with which step, in which order.
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from bottles.backend.managers import dependency as dependency_module  # noqa: E402
from bottles.backend.managers import installer as installer_module  # noqa: E402
from bottles.backend.managers.dependency import DependencyManager  # noqa: E402
from bottles.backend.managers.installer import InstallerManager  # noqa: E402
from bottles.backend.models.config import BottleConfig  # noqa: E402
from bottles.backend.models.result import Result  # noqa: E402
from bottles.backend.umu.dependencies import UmuDependencyInstaller  # noqa: E402

# handler name -> opcode name from the spec tables, and what each handler
# returns on success so the old dispatcher keeps going
DEPENDENCY_HANDLERS = {
    "download_archive": ("download_archive", True),
    "install_exe_msi": ("install_file", Result(True)),
    "uninstall": ("uninstall", True),
    "cab_extract": ("cab_extract", True),
    "get_from_cab": ("get_from_cab", True),
    "archive_extract": ("archive_extract", True),
    "install_fonts": ("install_fonts", True),
    "copy_dll": ("copy_file", True),
    "register_dll": ("register_dll", True),
    "override_dll": ("override_dll", True),
    "set_register_key": ("set_register_key", True),
    "register_font": ("register_font", True),
    "replace_font": ("replace_font", True),
    "set_windows": ("set_windows", True),
    "use_windows": ("use_windows", True),
    "delete_dlls": ("delete_dlls", True),
}
# step spelling -> opcode, for steps the old loop skipped on arch
DEPENDENCY_SPELLINGS = {
    "install_exe": "install_file",
    "install_msi": "install_file",
    "copy_dll": "copy_file",
    "copy_file": "copy_file",
    "install_cab_fonts": "install_fonts",
    "install_fonts": "install_fonts",
    **{name: opcode for name, (opcode, _value) in DEPENDENCY_HANDLERS.items() if name != "install_exe_msi"},
}
INSTALLER_HANDLERS = {
    "run_script": "run_script",
    "run_winecommand": "run_winecommand",
    "update_config": "update_config",
}
INSTALLER_LEGACY = {"set_windows", "check_file_exist"}


def fields_of(step: dict) -> dict:
    return {key: step[key] for key in sorted(step) if key not in ("action", "for")}


def load_corpus(root: Path):
    for path in sorted(root.rglob("*.yml")):
        with open(path) as handle:
            manifest = yaml.safe_load(handle)
        yield str(path.relative_to(root)), manifest


def capture_dependencies(corpus: Path):
    # every manifest is preloaded so prerequisite and cycle lookups never
    # reach the repository
    entries = list(load_corpus(corpus))
    manifests = {(manifest["Name"], False): manifest for _p, manifest in entries}
    for path, manifest in entries:
        for arch in ("win64", "win32"):
            executed: list[dict] = []
            handlers: list[str] = []
            flags: list[bool] = []

            def describe(step, _executed=executed):
                _executed.append(step)
                return "step"

            def handler_for(name, value):
                def recorder(*_args, **_kwargs):
                    handlers.append(name)
                    return value

                return recorder

            saved = {}
            saved["perform"] = DependencyManager._DependencyManager__perform_steps

            def perform(self, *args, _original=saved["perform"], **kwargs):
                res = _original(self, *args, **kwargs)
                if res.ok:
                    flags.append(bool(res.data.get("uninstaller")))
                return res

            DependencyManager._DependencyManager__perform_steps = perform
            saved["describe"] = DependencyManager._DependencyManager__describe_step
            DependencyManager._DependencyManager__describe_step = staticmethod(describe)
            for name, (_opcode, value) in DEPENDENCY_HANDLERS.items():
                attr = f"_DependencyManager__step_{name}"
                saved[attr] = getattr(DependencyManager, attr)
                setattr(DependencyManager, attr, handler_for(name, value))
            saved["rules"] = dependency_module.RegistryRuleManager.apply_rules
            dependency_module.RegistryRuleManager.apply_rules = staticmethod(
                lambda _config, trigger: None
            )
            try:
                manager = SimpleNamespace(
                    component_manager=SimpleNamespace(download=lambda **_k: Result(True)),
                    update_config=lambda *_a, **_k: None,
                    versioning_manager=SimpleNamespace(create_state=lambda **_k: None),
                    supported_dependencies={},
                )
                dm = DependencyManager.__new__(DependencyManager)
                dm._DependencyManager__manager = manager
                dm._DependencyManager__manifests = dict(manifests)
                dm._DependencyManager__repo = SimpleNamespace(get=lambda _name, _plain=False: False)
                dm._DependencyManager__offline = False
                config = BottleConfig(Name="golden", Arch=arch, Path="golden")
                result = dm.install(config, [manifest["Name"], {}])
            finally:
                DependencyManager._DependencyManager__perform_steps = saved.pop("perform")
                DependencyManager._DependencyManager__describe_step = saved.pop("describe")
                dependency_module.RegistryRuleManager.apply_rules = saved.pop("rules")
                for attr, value in saved.items():
                    setattr(DependencyManager, attr, value)

            line = {"path": path, "name": manifest["Name"], "kind": "dependency", "arch": arch}
            if not result.ok and not executed:
                line.update(parse=f"error: {result.message}", uninstaller=None, ops=[])
                yield line
                continue
            assert result.ok, (path, arch, result.message)
            assert len(handlers) == len(executed), (path, arch, handlers, executed)
            ops = []
            index = 0
            for step in manifest["Steps"]:
                if index < len(executed) and executed[index] is step:
                    opcode = DEPENDENCY_HANDLERS[handlers[index]][0]
                    index += 1
                    ops.append({"op": opcode, "skipped": False, "fields": fields_of(step)})
                else:
                    opcode = DEPENDENCY_SPELLINGS[step["action"]]
                    ops.append({"op": opcode, "skipped": True, "fields": fields_of(step)})
            assert index == len(executed), (path, arch, index, len(executed), handlers, [s.get("action") for s in manifest["Steps"]], [s.get("action") for s in executed])
            # the machine-level flag: every executed step kept the uninstaller.
            # (install() then discards it unless the manifest names an Uninstaller.)
            line.update(parse="ok", uninstaller=all(flags), ops=ops)
            yield line


def installer_well_configured(manifest: dict) -> bool:
    # the executable check from InstallerManager.install, copied so the golden
    # records the same accept/reject verdict without running the whole flow
    executables = manifest.get("Executables")
    if executables is None:
        executables = [manifest.get("Executable")]
    return bool(
        isinstance(executables, list)
        and executables
        and all(isinstance(item, dict) and item.get("file") and item.get("name") for item in executables)
    )


def capture_installers(corpus: Path):
    for path, manifest in load_corpus(corpus):
        line = {"path": path, "name": manifest["Name"], "kind": "installer", "arch": "win64"}
        if not installer_well_configured(manifest):
            line.update(parse="error: Installer is not well configured.", uninstaller=None, ops=[])
            yield line
            continue

        events: list[str] = []

        class FakeExecutor:
            def __init__(self, _config, **_kwargs):
                events.append("installer_run_file")

            def run(self):
                return Result(True)

        def handler_for(opcode):
            def recorder(*_args, **_kwargs):
                events.append(opcode)
                return Result(True)

            return recorder

        saved = {"WineExecutor": installer_module.WineExecutor}
        installer_module.WineExecutor = FakeExecutor
        for name, opcode in INSTALLER_HANDLERS.items():
            attr = f"_InstallerManager__step_{name}"
            saved[attr] = getattr(InstallerManager, attr)
            setattr(InstallerManager, attr, handler_for(opcode))
        try:
            im = InstallerManager.__new__(InstallerManager)
            im._InstallerManager__manager = SimpleNamespace()
            im._InstallerManager__component_manager = SimpleNamespace(download=lambda *_a, **_k: True)
            im._InstallerManager__local_resources = defaultdict(lambda: "/nonexistent/local.exe")
            config = BottleConfig(Name="golden", Arch="win64", Path="golden")
            ops = []
            for step in manifest.get("Steps") or []:
                events.clear()
                result = im._InstallerManager__perform_steps(config, [step])
                assert result is None or result.ok, (path, step, result.message)
                if events:
                    assert len(events) == 1, (path, step, events)
                    opcode = events[0]
                else:
                    assert step["action"] in INSTALLER_LEGACY, (path, step)
                    opcode = f"legacy_{step['action']}"
                ops.append({"op": opcode, "skipped": False, "fields": fields_of(step)})
        finally:
            installer_module.WineExecutor = saved.pop("WineExecutor")
            for attr, value in saved.items():
                setattr(InstallerManager, attr, value)
        line.update(parse="ok", uninstaller=None, ops=ops)
        yield line


def capture_umu(corpus: Path):
    manifests = {manifest["Name"]: manifest for _path, manifest in load_corpus(corpus)}
    manager = SimpleNamespace(
        dependency_manager=SimpleNamespace(get_dependency=lambda name: manifests.get(name))
    )
    installer = UmuDependencyInstaller(manager, repository=None, executor=None)
    for path, manifest in load_corpus(corpus):
        try:
            installer._plan((manifest["Name"],), set())
            verdict = "ok"
        except Exception as error:  # the message is the contract
            verdict = f"error: {error}"
        yield {
            "path": path,
            "name": manifest["Name"],
            "kind": "dependency",
            "arch": None,
            "parse": verdict,
            "uninstaller": None,
            "ops": [],
        }


def write(lines, target: Path):
    with open(target, "w") as handle:
        for line in lines:
            handle.write(json.dumps(line, sort_keys=True) + "\n")


def main(argv):
    corpus, golden = Path(argv[1]), Path(argv[2])
    golden.mkdir(parents=True, exist_ok=True)
    write(capture_dependencies(corpus / "dependencies"), golden / "dependencies.jsonl")
    write(capture_installers(corpus / "installers"), golden / "installers.jsonl")
    write(capture_umu(corpus / "dependencies"), golden / "umu.jsonl")


if __name__ == "__main__":
    main(sys.argv)
