# A test executor that records every call instead of touching a bottle, and
# renders a run in the golden trace format produced by legacy_capture.py.
import json
from typing import Optional

from bottles.backend.models.result import Result
from bottles.backend.steps.grammar import ALL_OPCODES, Op, Program, ProgramKind
from bottles.backend.steps.machine import Callbacks, run


class RecordingExecutor:
    def __init__(self, fail_on: Optional[set[str]] = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = set(fail_on or ())

    def _record(self, op: Op) -> Result:
        self.calls.append((op.opcode, op.fields()))
        if op.opcode in self.fail_on:
            return Result(False, message="boom")
        return Result(True)

    def trace(self, program: Program, arch: str, path: Optional[str] = None) -> dict:
        self.calls.clear()
        result = run(program, self, arch, Callbacks())
        executed = iter(self.calls)
        ops = []
        for op in program.ops:
            skipped = not op.applies_to(arch)
            if not skipped:
                assert next(executed)[0] == op.opcode
            ops.append({"op": op.opcode, "skipped": skipped, "fields": _plain(op.fields())})
        line = {
            "name": program.header.name,
            "kind": program.kind.value,
            "arch": arch,
            "parse": "ok",
            "uninstaller": result.data["uninstaller"] if program.kind is ProgramKind.DEPENDENCY else None,
            "ops": ops,
        }
        if path is not None:
            line["path"] = path
        return line


def _plain(value):
    # tuples become lists and dict keys sort, exactly as json.dumps(sort_keys)
    # wrote the golden files
    return json.loads(json.dumps(value, sort_keys=True))


for _cls in ALL_OPCODES:
    setattr(RecordingExecutor, _cls.opcode, RecordingExecutor._record)
