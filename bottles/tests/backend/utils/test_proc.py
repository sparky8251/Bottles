import pytest

from bottles.backend.utils import proc as proc_module
from bottles.backend.utils.proc import Proc, ProcUtils


def test_kill_signals_the_pid_directly(monkeypatch):
    sent = []
    monkeypatch.setattr(proc_module.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    Proc("4242").kill()
    Proc("4243").kill(9)

    assert sent == [(4242, 15), (4243, 9)]


@pytest.mark.parametrize("exc", [ProcessLookupError, PermissionError])
def test_kill_ignores_gone_or_foreign_processes(monkeypatch, exc):
    def raise_it(_pid, _sig):
        raise exc()

    monkeypatch.setattr(proc_module.os, "kill", raise_it)
    Proc("1").kill()  # must not raise


def test_get_by_name_matches_the_process_name_field(monkeypatch):
    class FakeProc:
        def __init__(self, pid, name):
            self.pid, self._name = pid, name

        def get_name(self):
            return self._name

    procs = [FakeProc("1", "wineserver"), FakeProc("2", "vmtouch"), FakeProc("3", "bash")]
    monkeypatch.setattr(ProcUtils, "get_procs", staticmethod(lambda: procs))

    assert [p.pid for p in ProcUtils.get_by_name("wineserver")] == ["1"]
    assert [p.pid for p in ProcUtils.get_by_name("vmtouch")] == ["2"]
    assert ProcUtils.get_by_name("explorer") == []
