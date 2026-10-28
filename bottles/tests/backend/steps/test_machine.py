from bottles.backend.steps.grammar import (
    OPCODES,
    CabExtract,
    DependencyHeader,
    InstallerHeader,
    InstallFile,
    OverrideDll,
    Program,
    ProgramKind,
    RegisterDll,
    RunScript,
)
from bottles.backend.steps.machine import Callbacks, run
from bottles.tests.backend.steps.recording import RecordingExecutor


def _program(*ops):
    return Program(ProgramKind.DEPENDENCY, DependencyHeader("d"), ops)


def test_skips_ops_whose_arches_exclude_the_bottle():  # Review Focus 2
    program = _program(
        InstallFile(url="u", file_name="x86.exe"),
        InstallFile(url="u", file_name="x64.exe", arches=("win64",)),
        RegisterDll(dlls=("a",), arches="win32"),
    )
    executor = RecordingExecutor()
    assert run(program, executor, "win64").ok
    assert [fields.get("file_name", fields.get("dlls")) for _op, fields in executor.calls] == ["x86.exe", "x64.exe"]
    executor = RecordingExecutor()
    assert run(program, executor, "win32").ok
    assert [fields.get("file_name", fields.get("dlls")) for _op, fields in executor.calls] == ["x86.exe", ("a",)]


def test_halting_failure_returns_the_executor_result_and_stops():
    program = _program(InstallFile(url="u", file_name="a.exe"), RegisterDll(dlls=("a",)))
    executor = RecordingExecutor(fail_on={"install_file"})
    result = run(program, executor, "win64")
    assert not result.ok and result.message == "boom"
    assert [op for op, _ in executor.calls] == ["install_file"]


def test_non_halting_failure_continues():
    program = _program(OverrideDll(dll="a", type="native"), RegisterDll(dlls=("a",)))
    executor = RecordingExecutor(fail_on={"override_dll"})
    result = run(program, executor, "win64")
    assert result.ok and result.data == {"uninstaller": True}
    assert [op for op, _ in executor.calls] == ["override_dll", "register_dll"]


def test_uninstaller_flag_clears_on_extract_ops_only():
    assert run(_program(InstallFile(url="u", file_name="a.exe")), RecordingExecutor(), "win64").data == {"uninstaller": True}
    assert run(_program(CabExtract(url="u", file_name="a.cab")), RecordingExecutor(), "win64").data == {"uninstaller": False}
    # a skipped extract step does not clear it
    assert run(_program(CabExtract(url="u", file_name="a.cab", arches=("win32",))), RecordingExecutor(), "win64").data == {
        "uninstaller": True
    }


def test_callbacks_fire_in_order_and_not_for_skipped_ops():
    program = Program(
        ProgramKind.INSTALLER,
        InstallerHeader("i"),
        (RunScript(script="true"), RunScript(script="false", arches=("win32",))),
    )
    events = []
    callbacks = Callbacks(
        progress=lambda text: events.append(("progress", text)),
        fraction=lambda value: events.append(("fraction", value)),
        step=lambda: events.append(("step",)),
    )
    executor = RecordingExecutor()
    assert run(program, executor, "win64", callbacks).ok
    assert events == [("step",), ("progress", "installer script"), ("fraction", None)]
    assert len(executor.calls) == 1


def test_recording_executor_defines_every_opcode():
    for table in OPCODES.values():
        for cls in set(table.values()):
            assert callable(getattr(RecordingExecutor, cls.opcode))


def test_trace_matches_the_golden_line_shape():
    program = _program(
        InstallFile(url="u", file_name="a.exe", arguments="/q"),
        CabExtract(url="u", file_name="b.cab", arches=("win32",)),
    )
    assert RecordingExecutor().trace(program, "win64", path="x.yml") == {
        "path": "x.yml",
        "name": "d",
        "kind": "dependency",
        "arch": "win64",
        "parse": "ok",
        "uninstaller": True,
        "ops": [
            {"op": "install_file", "skipped": False, "fields": {"arguments": "/q", "file_name": "a.exe", "url": "u"}},
            {"op": "cab_extract", "skipped": True, "fields": {"file_name": "b.cab", "url": "u"}},
        ],
    }
