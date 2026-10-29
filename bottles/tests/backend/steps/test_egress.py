# The egress oracle: every corpus manifest run through the new executors with
# every side-effect boundary faked (egress.py) must make the same calls, with
# the same repr'd arguments, in the same order, as the old handlers did
# (golden/egress-*.jsonl, captured by legacy_egress_capture.py at the
# pre-refactor commit).
#
# What this cannot see: the fakes always succeed, so failure branches that
# depend on a real archive, registry or Wine process are not exercised, and a
# step that both sides fail at the same index for a filesystem reason is
# compared only up to that point.
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bottles.backend import globals as globals_module
from bottles.backend.models.config import BottleConfig
from bottles.backend.steps.executors import bottle as bottle_module
from bottles.backend.steps.executors import umu as umu_executor_module
from bottles.backend.steps.executors.bottle import BottleStepExecutor
from bottles.backend.steps.grammar import ProgramKind
from bottles.backend.steps.machine import run
from bottles.backend.steps.yaml_frontend import parse
from bottles.backend.umu.dependencies import UmuDependencyInstaller
from bottles.backend.umu.repository import UmuGameRepository
from bottles.tests.backend.steps import egress

HERE = Path(__file__).parent
CORPUS = HERE / "corpus"
GOLDEN = HERE / "golden"


def load_golden(name):
    with open(GOLDEN / name) as handle:
        return [json.loads(line) for line in handle]


def manifest_at(folder, path):
    with open(CORPUS / folder / path) as handle:
        return yaml.safe_load(handle)


def fresh(tmp_path):
    temp = str(tmp_path / "temp")
    os.makedirs(temp)
    return egress.make_prefix(str(tmp_path)), temp


DEPENDENCIES = load_golden("egress-dependencies.jsonl")
INSTALLERS = load_golden("egress-installers.jsonl")
UMU = load_golden("egress-umu.jsonl")


@pytest.mark.parametrize("line", DEPENDENCIES, ids=[f"{l['path']}@{l['arch']}" for l in DEPENDENCIES])
def test_dependency_egress_matches_the_old_handlers(line, tmp_path):
    prefix, temp = fresh(tmp_path)
    rec = egress.Recorder(prefix, temp)
    manifest = manifest_at("dependencies", line["path"])
    program = parse(manifest, ProgramKind.DEPENDENCY, line["path"])
    with egress.fake_environment([bottle_module], rec, globals_module) as manager:
        config = BottleConfig(Name="golden", Arch=line["arch"], Path=prefix, Custom_Path=True)
        executor = BottleStepExecutor(manager, config)
        failed_at = None
        # one op at a time so the failing index is known, as the capture did
        for index, op in enumerate(program.ops):
            if not op.applies_to(config.Arch):
                continue
            try:
                res = run(replace(program, ops=(op,)), executor, config.Arch)
            except Exception as error:
                rec.calls.append({"call": "<exception>", "args": repr(type(error).__name__), "kwargs": "{}"})
                failed_at = index
                break
            if not res.ok:
                failed_at = index
                break
    assert {"failed_at": failed_at, "calls": rec.calls} == {"failed_at": line["failed_at"], "calls": line["calls"]}


@pytest.mark.parametrize("line", INSTALLERS, ids=[l["path"] for l in INSTALLERS])
def test_installer_egress_matches_the_old_handlers(line, tmp_path):
    prefix, temp = fresh(tmp_path)
    rec = egress.Recorder(prefix, temp)
    manifest = manifest_at("installers", line["path"])
    steps = manifest.get("Steps") or []
    with egress.fake_environment([bottle_module], rec, globals_module) as manager:
        config = BottleConfig(Name="golden", Arch="win64", Path=prefix, Custom_Path=True)
        executor = BottleStepExecutor(manager, config, local_resources=egress.local_resources())
        failed_at = None
        for index, step in enumerate(steps):
            # the installer validator needs an executable block; the steps are
            # what is under test, so each is lowered on its own
            program = parse(
                {"Name": "x", "Executable": {"file": "x.exe", "name": "X"}, "Steps": [step]},
                ProgramKind.INSTALLER,
                "x",
            )
            try:
                res = run(program, executor, None)
            except Exception as error:
                rec.calls.append({"call": "<exception>", "args": repr(type(error).__name__), "kwargs": "{}"})
                failed_at = index
                break
            if not res.ok:
                failed_at = index
                break
    assert {"failed_at": failed_at, "calls": rec.calls} == {"failed_at": line["failed_at"], "calls": line["calls"]}


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


@pytest.mark.parametrize("line", UMU, ids=[l["path"] for l in UMU])
def test_umu_egress_matches_the_old_installer(line, tmp_path):
    prefix, temp = fresh(tmp_path)
    rec = egress.Recorder(prefix, temp)
    manifests = {}
    for path in (CORPUS / "dependencies").rglob("*.yml"):
        with open(path) as handle:
            manifest = yaml.safe_load(handle)
        manifests[manifest["Name"]] = manifest
    name = manifest_at("dependencies", line["path"])["Name"]
    with egress.fake_environment([umu_executor_module], rec, globals_module) as manager:
        manager.dependency_manager = SimpleNamespace(get_dependency=lambda n: manifests.get(n))
        repository = UmuGameRepository(Path(prefix) / "umu")
        exe = Path(prefix) / "game.exe"
        exe.write_bytes(b"game")
        game = repository.new_game("Game", exe, proton="GE-Proton")
        fake_repository = SimpleNamespace(update=lambda g, **kw: replace(g, **kw))
        installer = UmuDependencyInstaller(manager, fake_repository, ProcessExecutor(rec))
        assert installer.is_compatible(name)
        result = installer.install(game, [name])
        rec.record("umu.result", ok=result.ok, message=result.message, environment=result.data.environment)
    assert rec.calls == line["calls"]
