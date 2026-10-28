# Type-check-only module: assigning each executor to the Executor Protocol
# makes a missing or mis-typed opcode method a mypy error (mypy.ini enables
# the assignment check for this module). Nothing imports this at runtime.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bottles.backend.models.config import BottleConfig
    from bottles.backend.steps.executors.bottle import BottleStepExecutor
    from bottles.backend.steps.executors.umu import UmuStepExecutor
    from bottles.backend.steps.machine import Executor

    _bottle: Executor = BottleStepExecutor(None, BottleConfig())
    _umu: Executor = UmuStepExecutor(None, None, None, {})
