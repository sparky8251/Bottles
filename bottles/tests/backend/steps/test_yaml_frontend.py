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
    LegacyNoop,
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


def test_lax_lowers_an_unknown_action_to_a_no_op_as_the_old_loops_did():
    program = parse(
        {"Steps": [{"action": "run_script", "script": "true", "for": ["win64"]}]},
        ProgramKind.DEPENDENCY,
        "dep",
    )
    assert [type(op) for op in program.ops] == [LegacyNoop]
    assert program.ops[0].spelling == "run_script" and program.ops[0].arches == ("win64",)
    assert program.ops[0].describe() == "Running run script..."  # the old fallback text
    program = parse(_installer(Steps=[{"action": "override_dll", "dll": "x"}]), ProgramKind.INSTALLER, "x")
    assert program.ops[0].opcode == "legacy_noop" and program.ops[0].spelling == "override_dll"


def test_strict_rejects_an_action_outside_its_program_kind():
    with pytest.raises(ManifestError, match=r"Invalid manifest for dep: step 1 \(run_script\): unknown action\."):
        parse({"Steps": [{"action": "run_script", "script": "true"}]}, ProgramKind.DEPENDENCY, "dep", strict=True)
    with pytest.raises(ManifestError, match="Installer is not well configured"):
        parse(_installer(Steps=[{"action": "override_dll", "dll": "x"}]), ProgramKind.INSTALLER, "x", strict=True)


def test_the_two_legacy_spellings_lower_lax_and_fail_strict():
    manifest = _installer(
        Steps=[
            {"action": "set_windows", "version": "win10"},
            {"action": "check_file_exist", "path": "homedir/.itch/itch"},
        ]
    )
    program = parse(manifest, ProgramKind.INSTALLER, "x")
    assert [type(op) for op in program.ops] == [LegacySetWindows, LegacyCheckFileExist]
    with pytest.raises(ManifestError, match="Installer is not well configured"):
        parse(manifest, ProgramKind.INSTALLER, "x", strict=True)


def test_lax_drops_a_key_no_opcode_defines_and_strict_rejects_it():
    manifest = {"Steps": [{"action": "register_dll", "dlls": ["a"], "bogus": 1}]}
    program = parse(manifest, ProgramKind.DEPENDENCY, "dep")
    assert program.ops[0].fields() == {"dlls": ("a",)}
    with pytest.raises(ManifestError, match=r"Invalid manifest for dep: step 1 \(register_dll\): unknown key 'bogus'\."):
        parse(manifest, ProgramKind.DEPENDENCY, "dep", strict=True)


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
                if path.name not in ("starcitizen.yml", "itchio.yml"):
                    parse_file(str(path), kind, strict=True)
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


def test_list_values_keep_their_python_type_unless_the_opcode_iterates_them():
    # review C1: gfw.yml writes `arguments: [/nodotnet /q]`; the handler
    # forwards the value into an f-string, so a tuple would render differently
    program = parse(
        {
            "Steps": [
                {"action": "install_exe", "url": "u", "file_name": "f.exe", "arguments": ["/nodotnet /q"]},
                {"action": "register_dll", "dlls": ["a", "b"]},
            ]
        },
        ProgramKind.DEPENDENCY,
        "gfw",
    )
    assert program.ops[0].arguments == ["/nodotnet /q"] and type(program.ops[0].arguments) is list
    assert program.ops[1].dlls == ("a", "b")
    gfw = parse_file(str(CORPUS / "dependencies/Essentials/gfw.yml"), ProgramKind.DEPENDENCY)
    assert f"{gfw.ops[0].arguments}" == "['/nodotnet /q']"


def test_missing_url_is_absent_not_empty():
    # review I1: umu refused an install_exe without a url as "invalid download"
    program = parse({"Steps": [{"action": "install_exe", "file_name": "a.exe"}]}, ProgramKind.DEPENDENCY, "d")
    assert program.ops[0].url is None
    with pytest.raises(ManifestError, match="has an invalid download"):
        check_umu_target(program)


@pytest.mark.parametrize(
    "key", ["arches", "spelling", "opcode", "spellings", "kind", "keeps_uninstaller", "halts_on_failure"]
)
def test_op_attribute_names_are_not_manifest_keys(key):
    # reviews I2 and I-A: manifest keys and Op attributes are two namespaces;
    # a key spelled like an attribute is an unknown key (dropped lax, rejected
    # strict), never a way to set the attribute and never a TypeError
    manifest = {"Steps": [{"action": "register_dll", "dlls": ["a"], key: "win32"}]}
    op = parse(manifest, ProgramKind.DEPENDENCY, "d").ops[0]
    assert op.applies_to("win64") and op.spelling == "register_dll" and op.opcode == "register_dll"
    assert op.kind is ProgramKind.DEPENDENCY and op.halts_on_failure is False
    with pytest.raises(ManifestError, match=r"Invalid manifest for d: step 1 \(register_dll\): unknown key"):
        parse(manifest, ProgramKind.DEPENDENCY, "d", strict=True)


def test_missing_iterated_or_string_fields_are_absent_so_the_bodies_fail_as_before():
    # review M-A..M-C: the old handlers crashed on a missing fonts/script/path;
    # a default of ()/"" would have turned that into a silent success
    program = parse(
        {"Steps": [{"action": "install_fonts", "url": "temp/x"}]}, ProgramKind.DEPENDENCY, "d"
    )
    assert program.ops[0].fonts is None
    program = parse(
        _installer(Steps=[{"action": "run_script"}, {"action": "update_config", "type": "ini"}]),
        ProgramKind.INSTALLER,
        "x",
    )
    assert program.ops[0].script is None and program.ops[1].path is None


def test_installer_run_file_requires_a_url():
    # review M-D: the old loop raised KeyError('url') before any side effect;
    # the validator now refuses the manifest instead (documented)
    with pytest.raises(ManifestError, match="Installer is not well configured"):
        parse(_installer(Steps=[{"action": "install_exe", "file_name": "a.exe"}]), ProgramKind.INSTALLER, "x")


def test_installer_dependencies_pass_through_as_written():
    # note 6: a bad entry used to reach the dependency manager and fail with
    # "Failed to install 3. Cannot find manifest for 3."; it must not be dropped
    program = parse(_installer(Dependencies=["vcredist2010", 3]), ProgramKind.INSTALLER, "x")
    assert program.header.dependencies == ("vcredist2010", 3)
    with pytest.raises(ManifestError, match="Installer is not well configured"):
        parse(_installer(Dependencies="vcredist2010"), ProgramKind.INSTALLER, "x")


def test_uninstall_requires_a_file_name():
    # note 8: the old dispatcher indexed step["file_name"] and crashed
    with pytest.raises(ManifestError, match=r"Invalid manifest for d\."):
        parse({"Steps": [{"action": "uninstall"}]}, ProgramKind.DEPENDENCY, "d")


def test_collect_registers_subclasses_of_subclasses():
    # note 7; the class is unregistered afterwards so it cannot reach
    # Op.__subclasses__() in a later _collect()
    import gc

    from bottles.backend.steps import grammar

    class Nested(grammar.SetWindows):
        opcode = "nested_set_windows"
        spellings = ("nested_set_windows",)

    try:
        assert grammar._collect()[ProgramKind.DEPENDENCY]["nested_set_windows"] is Nested
        assert Nested.manifest_fields == {"version": False, "executable": False}
    finally:
        del Nested
        gc.collect()
    assert "nested_set_windows" not in grammar._collect()[ProgramKind.DEPENDENCY]


def test_strict_errors_say_where_they_stopped():
    with pytest.raises(ManifestError) as e:
        parse(
            _installer(Steps=[{"action": "run_script", "script": "true"}, {"action": "set_windows", "version": "win10"}]),
            ProgramKind.INSTALLER,
            "x",
            strict=True,
        )
    assert e.value.message == "Installer is not well configured: step 2 (set_windows): unknown action."
    with pytest.raises(ManifestError) as e:
        parse({"Steps": [{"action": "register_dll", "dlls": ["a"], "bogus": 1}]}, ProgramKind.DEPENDENCY, "d", strict=True)
    assert e.value.message == "Invalid manifest for d: step 1 (register_dll): unknown key 'bogus'."
