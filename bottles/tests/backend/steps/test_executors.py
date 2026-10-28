import pytest

from bottles.backend.steps.executors.bottle import BottleStepExecutor
from bottles.backend.steps.executors.umu import UmuStepExecutor
from bottles.backend.steps.grammar import ALL_OPCODES
from bottles.tests.backend.steps.recording import RecordingExecutor


@pytest.mark.parametrize("executor", [BottleStepExecutor, UmuStepExecutor, RecordingExecutor])
def test_executor_defines_every_opcode(executor):
    missing = [cls.opcode for cls in ALL_OPCODES if not callable(getattr(executor, cls.opcode, None))]
    assert missing == []
    assert len(ALL_OPCODES) == 23
