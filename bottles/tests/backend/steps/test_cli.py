import subprocess
import sys
from pathlib import Path

CORPUS = Path(__file__).parent / "corpus"
REPO = Path(__file__).resolve().parents[4]


def _run(*args: str) -> subprocess.CompletedProcess:
    # -I: no PYTHONPATH, no cwd; the repo root is added explicitly so only
    # the steps package and its stdlib/PyYAML imports can satisfy the run
    code = f"import sys; sys.path.insert(0, {str(REPO)!r}); import runpy; runpy.run_module('bottles.backend.steps', run_name='__main__')"
    return subprocess.run(
        [sys.executable, "-I", "-c", code, *args], capture_output=True, text=True
    )


def test_dump_runs_without_gtk_and_prints_one_line_per_op():
    result = _run("dump", "dependency", str(CORPUS / "dependencies/Essentials/vcredist2010.yml"))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 6
    assert lines[0].startswith("install_file ")
    assert lines[1].startswith('install_file for=["win64"] ')
    assert lines[2:] == [
        "override_dll dll=msvcp100 type=native,builtin",
        "override_dll dll=msvcr100 type=native,builtin",
        "override_dll dll=vcomp100 type=native,builtin",
        "override_dll dll=atl100 type=native,builtin",
    ]


def test_dump_shows_the_legacy_no_op():
    result = _run("dump", "installer", str(CORPUS / "installers/Games/starcitizen.yml"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "legacy_set_windows version=win10"


def test_check_reports_a_malformed_manifest_on_stderr():
    result = _run("check", "installer", str(CORPUS / "installers/Software/amazonmusic.yml"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "Installer is not well configured."


def test_check_passes_quietly():
    result = _run("check", "dependency", str(CORPUS / "dependencies/Essentials/dotnet472.yml"))
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""


def test_strict_rejects_what_lax_tolerates():
    path = str(CORPUS / "installers/Games/starcitizen.yml")
    assert _run("check", "installer", path).returncode == 0
    strict = _run("check", "installer", path, "--strict")
    assert strict.returncode == 1
    assert strict.stderr.strip() == "Installer is not well configured."
