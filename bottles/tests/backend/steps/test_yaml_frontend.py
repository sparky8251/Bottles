import json
import subprocess
import sys
from pathlib import Path

import pytest

from bottles.backend.steps.grammar import (
    ARCH_BOTH,
    CopyFile,
    InstallFile,
    LegacyCheckFileExist,
    LegacySetWindows,
    ProgramKind,
)
from bottles.backend.steps.yaml_frontend import ManifestError, check_umu_target, parse, parse_file

CORPUS = Path(__file__).parent / "corpus"
REPO = Path(__file__).resolve().parents[4]


def _installer(**extra):
    return {"Name": "x", "Executable": {"file": "x.exe", "name": "X"}, "Steps": [], **extra}


def test_rejects_non_dict_step_with_the_ui_message():  # Review Focus 1
    with pytest.raises(ManifestError) as e:
        parse({"Steps": ["x"]}, ProgramKind.DEPENDENCY, "dep")
    assert e.value.message == "Invalid manifest for dep."
    with pytest.raises(ManifestError) as e:
        parse(None, ProgramKind.DEPENDENCY, "dep")
    assert e.value.message == "Cannot find manifest for dep."


def test_rejects_installer_action_in_dependency_kind():
    with pytest.raises(ManifestError, match=r"Invalid manifest for dep\."):
        parse({"Steps": [{"action": "run_script", "script": "true"}]}, ProgramKind.DEPENDENCY, "dep")


def test_rejects_unknown_installer_action_but_accepts_the_two_legacy_spellings():
    with pytest.raises(ManifestError, match="Installer is not well configured"):
        parse(_installer(Steps=[{"action": "override_dll", "dll": "x"}]), ProgramKind.INSTALLER, "x")
    program = parse(
        _installer(
            Steps=[
                {"action": "set_windows", "version": "win10"},
                {"action": "check_file_exist", "path": "homedir/.itch/itch"},
            ]
        ),
        ProgramKind.INSTALLER,
        "x",
    )
    assert [type(op) for op in program.ops] == [LegacySetWindows, LegacyCheckFileExist]


def test_rejects_a_key_no_opcode_defines():
    with pytest.raises(ManifestError, match=r"Invalid manifest for dep\."):
        parse({"Steps": [{"action": "register_dll", "dlls": ["a"], "bogus": 1}]}, ProgramKind.DEPENDENCY, "dep")


def test_synonyms_map_to_one_opcode_and_keep_their_spelling():
    program = parse(
        {
            "Steps": [
                {"action": "install_msi", "url": "u", "file_name": "f.msi"},
                {"action": "copy_dll", "url": "temp/x", "file_name": "a.dll", "dest": "win64"},
            ]
        },
        ProgramKind.DEPENDENCY,
        "dep",
    )
    assert [type(op) for op in program.ops] == [InstallFile, CopyFile]
    assert [op.spelling for op in program.ops] == ["install_msi", "copy_dll"]


def test_for_is_kept_as_written():
    program = parse(
        {
            "Steps": [
                {"action": "register_dll", "dlls": ["a"], "for": ["win64"]},
                {"action": "register_dll", "dlls": ["a"], "for": "win32"},
                {"action": "register_dll", "dlls": ["a"]},
            ]
        },
        ProgramKind.DEPENDENCY,
        "dep",
    )
    assert [op.arches for op in program.ops] == [("win64",), "win32", ARCH_BOTH]


def test_installer_requires_a_usable_executable_block():
    for executable in ({"lnk": "A.lnk", "path": "x"}, {"Name": "A", "File": "a.exe"}, None):
        with pytest.raises(ManifestError) as e:
            parse({"Name": "x", "Executable": executable, "Steps": []}, ProgramKind.INSTALLER, "x")
        assert e.value.message == "Installer is not well configured."


def test_umu_target_messages_match_the_old_validator():
    def dep(step):
        return parse({"Name": "d", "Steps": [step]}, ProgramKind.DEPENDENCY, "d")

    with pytest.raises(ManifestError, match="uses unsupported UMU action set_windows"):
        check_umu_target(dep({"action": "set_windows", "version": "win7"}))
    with pytest.raises(ManifestError, match="uses unsupported UMU action install_cab_fonts"):
        check_umu_target(dep({"action": "install_cab_fonts", "url": "temp/x", "fonts": ["a.ttf"]}))
    with pytest.raises(ManifestError, match="uses a temporary source"):
        check_umu_target(dep({"action": "install_exe", "url": "temp/x", "file_name": "a.exe"}))
    with pytest.raises(ManifestError, match="uses a dynamic DLL override"):
        check_umu_target(dep({"action": "override_dll", "dll": "a", "type": "native", "url": "temp/x"}))
    with pytest.raises(ManifestError, match="Invalid DLL override"):
        check_umu_target(dep({"action": "override_dll", "dll": "a"}))
    check_umu_target(dep({"action": "override_dll", "dll": "a", "type": "native,builtin"}))
    check_umu_target(dep({"action": "install_exe", "url": "https://x/a.exe", "file_name": "a.exe", "arguments": "/s"}))


def test_corpus_parses_and_the_two_legacy_steps_are_where_expected():
    legacy = {}
    for kind, folder in ((ProgramKind.DEPENDENCY, "dependencies"), (ProgramKind.INSTALLER, "installers")):
        for path in sorted((CORPUS / folder).rglob("*.yml")):
            try:
                program = parse_file(str(path), kind)
            except ManifestError as e:
                assert kind is ProgramKind.INSTALLER and e.message == "Installer is not well configured.", path
                continue
            for op in program.ops:
                if op.opcode.startswith("legacy_"):
                    legacy[path.name] = op.opcode
    assert legacy == {"starcitizen.yml": "legacy_set_windows", "itchio.yml": "legacy_check_file_exist"}


def test_frontend_imports_nothing_from_gtk_or_wine():
    code = (
        f"import sys; sys.path.insert(0, {str(REPO)!r}); import json\n"
        "import bottles.backend.steps.yaml_frontend\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith(('gi', 'bottles.backend.wine', 'bottles.backend.managers', 'bottles.frontend')))))"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []
