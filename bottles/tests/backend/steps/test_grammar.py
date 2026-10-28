from bottles.backend.steps.grammar import (
    OPCODES,
    CopyFile,
    DependencyHeader,
    InstallFile,
    LegacySetWindows,
    Program,
    ProgramKind,
    SetWindows,
)


def test_arches_substring_and_list_semantics():
    assert InstallFile(url="u", file_name="f").applies_to("win32")
    assert InstallFile(url="u", file_name="f", arches=("win64",)).applies_to("win64")
    assert not InstallFile(url="u", file_name="f", arches=("win64",)).applies_to("win32")
    assert InstallFile(url="u", file_name="f", arches="win64").applies_to("win64")
    assert not InstallFile(url="u", file_name="f", arches="win64").applies_to("win32")


def test_every_opcode_has_a_unique_name_and_kind_specific_spellings():
    classes = [cls for table in OPCODES.values() for cls in set(table.values())]
    names = [cls.opcode for cls in classes]
    assert len(names) == len(set(names)) == 22
    assert OPCODES[ProgramKind.DEPENDENCY]["install_msi"] is InstallFile
    assert OPCODES[ProgramKind.DEPENDENCY]["copy_dll"] is CopyFile
    assert OPCODES[ProgramKind.INSTALLER]["set_windows"] is LegacySetWindows
    assert "run_script" not in OPCODES[ProgramKind.DEPENDENCY]
    assert "override_dll" not in OPCODES[ProgramKind.INSTALLER]


def test_dump_is_one_line_per_op_in_order():
    ops = (
        SetWindows(version="win7"),
        InstallFile(url="https://x/a.exe", file_name="a.exe", arguments="/q /norestart", arches=("win64",)),
    )
    program = Program(ProgramKind.DEPENDENCY, DependencyHeader("d"), ops)
    assert program.dump().splitlines() == [
        "set_windows version=win7",
        'install_file for=["win64"] arguments="/q /norestart" file_name=a.exe url=https://x/a.exe',
    ]


def test_cacheable_downloads_skips_temp_urls_and_respects_arch():  # Review Focus 3
    ops = (
        InstallFile(url="https://x/a.exe", file_name="a.exe", file_checksum="1"),
        CopyFile(url="temp/pkg", file_name="b.dll", dest="win64"),
        InstallFile(
            url="https://x/c.exe", file_name="c.exe", rename="r.exe", file_checksum="2", arches=("win64",)
        ),
    )
    program = Program(ProgramKind.DEPENDENCY, DependencyHeader("d"), ops)
    assert program.cacheable_downloads("win32") == [("a.exe", "1")]
    assert program.cacheable_downloads("win64") == [("a.exe", "1"), ("r.exe", "2")]
    assert program.cacheable_downloads(None) == [("a.exe", "1"), ("r.exe", "2")]
