# The oracle: every manifest Bottles 67.5 fetches, run through parse and the
# machine with a recording executor, must produce the trace the old
# interpreters produced (golden/*.jsonl, captured by legacy_capture.py).
import functools
import json
from pathlib import Path

import pytest
import yaml

from bottles.backend.steps.grammar import ProgramKind
from bottles.backend.steps.yaml_frontend import ManifestError, parse_file
from bottles.tests.backend.steps.recording import RecordingExecutor

HERE = Path(__file__).parent
CORPUS = HERE / "corpus"
GOLDEN = HERE / "golden"


def load_golden(name: str) -> list[dict]:
    with open(GOLDEN / name) as handle:
        return [json.loads(line) for line in handle]


def _ids(lines):
    return [f"{line['path']}@{line['arch']}" for line in lines]


DEPENDENCIES = load_golden("dependencies.jsonl")
INSTALLERS = load_golden("installers.jsonl")


def _check(line: dict, kind: ProgramKind, folder: str):
    path = CORPUS / folder / line["path"]
    try:
        program = parse_file(str(path), kind)
    except ManifestError as error:
        assert line["parse"] == f"error: {error.message}"
        return
    assert line["parse"] == "ok"
    assert RecordingExecutor().trace(program, line["arch"], path=line["path"]) == line


@pytest.mark.parametrize("line", DEPENDENCIES, ids=_ids(DEPENDENCIES))
def test_dependency_trace_matches_the_old_interpreter(line):
    _check(line, ProgramKind.DEPENDENCY, "dependencies")


@pytest.mark.parametrize("line", INSTALLERS, ids=_ids(INSTALLERS))
def test_installer_trace_matches_the_old_interpreter(line):
    _check(line, ProgramKind.INSTALLER, "installers")


def test_the_two_legacy_steps_are_in_the_installer_traces():
    legacy = {
        line["path"]: [op["op"] for op in line["ops"] if op["op"].startswith("legacy_")]
        for line in INSTALLERS
        if any(op["op"].startswith("legacy_") for op in line["ops"])
    }
    assert legacy == {
        "Games/starcitizen.yml": ["legacy_set_windows"],
        "Games/itchio.yml": ["legacy_check_file_exist"],
    }


UMU = load_golden("umu.jsonl")


@functools.lru_cache(maxsize=None)
def _umu_installer():
    from types import SimpleNamespace

    from bottles.backend.umu.dependencies import UmuDependencyInstaller

    manifests = {}
    for line in UMU:
        with open(CORPUS / "dependencies" / line["path"], "rb") as handle:
            manifests[line["name"]] = yaml.safe_load(handle)
    manager = SimpleNamespace(
        dependency_manager=SimpleNamespace(get_dependency=lambda name: manifests.get(name))
    )
    return UmuDependencyInstaller(manager, repository=None, executor=None)


@pytest.mark.parametrize("line", UMU, ids=[line["path"] for line in UMU])
def test_umu_plan_verdict_matches_the_old_validator(line):
    from bottles.backend.umu.dependencies import UmuDependencyError

    installer = _umu_installer()
    try:
        installer._plan((line["name"],), set())
        verdict = "ok"
    except UmuDependencyError as error:
        verdict = f"error: {error}"
    assert verdict == line["parse"]
