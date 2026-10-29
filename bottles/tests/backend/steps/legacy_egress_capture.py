# Capture the egress golden traces from the step handlers as they were before
# the steps package existed. Run against a checkout of that commit:
#
#   git worktree add /tmp/claude-1000/old-31aabeb6 31aabeb6
#   python -I bottles/tests/backend/steps/legacy_egress_capture.py \
#       /tmp/claude-1000/old-31aabeb6 bottles/tests/backend/steps/corpus bottles/tests/backend/steps/golden
#
# Every side-effect boundary is replaced by the recorders in egress.py, the
# old dispatchers run each corpus manifest, and one JSON line per
# (manifest, arch) records the calls they made with repr'd arguments.
# test_egress.py replays the same manifests through the new executors.
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml

HERE = Path(__file__).resolve().parent
OLD_ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(OLD_ROOT))

spec = importlib.util.spec_from_file_location("egress", HERE / "egress.py")
egress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(egress)

from bottles.backend import globals as globals_module  # noqa: E402
from bottles.backend.managers import dependency as dependency_module  # noqa: E402
from bottles.backend.managers import installer as installer_module  # noqa: E402
from bottles.backend.models.config import BottleConfig  # noqa: E402
from bottles.backend.umu import dependencies as umu_module  # noqa: E402
from bottles.backend.umu.repository import UmuGameRepository  # noqa: E402

assert dependency_module.__file__.startswith(str(OLD_ROOT)), dependency_module.__file__


def load_corpus(root: Path):
    for path in sorted(root.rglob("*.yml")):
        with open(path) as handle:
            yield str(path.relative_to(root)), yaml.safe_load(handle)


def fresh():
    root = tempfile.mkdtemp(prefix="egress-")
    temp = os.path.join(root, "temp")
    os.makedirs(temp)
    return egress.make_prefix(root), temp


def capture_dependencies(corpus: Path):
    for path, manifest in load_corpus(corpus):
        for arch in ("win64", "win32"):
            prefix, temp = fresh()
            rec = egress.Recorder(prefix, temp)
            with egress.fake_environment([dependency_module], rec, globals_module) as manager:
                dm = dependency_module.DependencyManager.__new__(dependency_module.DependencyManager)
                dm._DependencyManager__manager = manager
                config = BottleConfig(Name="golden", Arch=arch, Path=prefix, Custom_Path=True)
                failed_at = None
                for index, step in enumerate(manifest["Steps"]):
                    if config.Arch not in step.get("for", "win64_win32"):
                        continue
                    try:
                        res = dm._DependencyManager__perform_steps(config, step)
                    except Exception as error:  # the old loop had no catch: record and stop
                        rec.calls.append({"call": "<exception>", "args": repr(type(error).__name__), "kwargs": "{}"})
                        failed_at = index
                        break
                    if not res.ok:
                        failed_at = index
                        break
            yield {"path": path, "kind": "dependency", "arch": arch, "failed_at": failed_at, "calls": rec.calls}


def capture_installers(corpus: Path):
    for path, manifest in load_corpus(corpus):
        prefix, temp = fresh()
        rec = egress.Recorder(prefix, temp)
        with egress.fake_environment([installer_module], rec, globals_module) as manager:
            im = installer_module.InstallerManager.__new__(installer_module.InstallerManager)
            im._InstallerManager__manager = manager
            im._InstallerManager__component_manager = manager.component_manager
            im._InstallerManager__local_resources = egress.local_resources()
            config = BottleConfig(Name="golden", Arch="win64", Path=prefix, Custom_Path=True)
            failed_at = None
            steps = manifest.get("Steps") or []
            for index, step in enumerate(steps):
                try:
                    res = im._InstallerManager__perform_steps(config, [step])
                except Exception as error:
                    rec.calls.append({"call": "<exception>", "args": repr(type(error).__name__), "kwargs": "{}"})
                    failed_at = index
                    break
                if res is not None and not res.ok:
                    failed_at = index
                    break
        yield {"path": path, "kind": "installer", "arch": "win64", "failed_at": failed_at, "calls": rec.calls}


class ProcessExecutor:
    def __init__(self, rec):
        self.rec = rec

    def run(self, game):
        self.rec.record(
            "umu.run",
            executable=str(game.executable),
            arguments=game.arguments,
            working_directory=str(game.working_directory),
            environment=game.environment,
        )
        return SimpleNamespace(wait=lambda: 0)

    def wait(self, game):
        self.rec.record("umu.wait")
        return 0


def capture_umu(corpus: Path):
    from dataclasses import replace

    entries = list(load_corpus(corpus))
    manifests = {m["Name"]: m for _p, m in entries}
    for path, manifest in entries:
        prefix, temp = fresh()
        rec = egress.Recorder(prefix, temp)
        with egress.fake_environment([umu_module], rec, globals_module) as manager:
            manager.dependency_manager = SimpleNamespace(get_dependency=lambda name: manifests.get(name))
            repository = UmuGameRepository(Path(prefix) / "umu")
            exe = Path(prefix) / "game.exe"
            exe.write_bytes(b"game")
            game = repository.new_game("Game", exe, proton="GE-Proton")
            fake_repository = SimpleNamespace(update=lambda g, **kw: replace(g, **kw))
            installer = umu_module.UmuDependencyInstaller(manager, fake_repository, ProcessExecutor(rec))
            if not installer.is_compatible(manifest["Name"]):
                continue
            result = installer.install(game, [manifest["Name"]])
            rec.record("umu.result", ok=result.ok, message=result.message, environment=result.data.environment)
        yield {"path": path, "kind": "umu", "arch": None, "failed_at": None, "calls": rec.calls}


def write(lines, target: Path):
    with open(target, "w") as handle:
        for line in lines:
            handle.write(json.dumps(line, sort_keys=True) + "\n")


def main(argv):
    corpus, golden = Path(argv[2]), Path(argv[3])
    write(capture_dependencies(corpus / "dependencies"), golden / "egress-dependencies.jsonl")
    write(capture_installers(corpus / "installers"), golden / "egress-installers.jsonl")
    write(capture_umu(corpus / "dependencies"), golden / "egress-umu.jsonl")


if __name__ == "__main__":
    main(sys.argv)
