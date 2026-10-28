import pytest

from bottles.backend.steps.executors.bottle import BottleStepExecutor
from bottles.backend.steps.executors.umu import UmuStepExecutor
from bottles.backend.steps.grammar import OPCODES
from bottles.tests.backend.steps.recording import RecordingExecutor


@pytest.mark.parametrize("executor", [BottleStepExecutor, UmuStepExecutor, RecordingExecutor])
def test_executor_defines_every_opcode(executor):
    missing = [
        cls.opcode
        for table in OPCODES.values()
        for cls in set(table.values())
        if not callable(getattr(executor, cls.opcode, None))
    ]
    assert missing == []
