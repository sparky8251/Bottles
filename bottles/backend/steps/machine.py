# The machine: one loop over a program's opcodes against an executor.
#
# The machine owns sequencing, the arch filter, the progress callbacks, the
# uninstaller flag and stop-on-failure. The executor owns side effects. It
# is the same loop for dependency and installer programs; what differs is
# which opcodes the program can contain, and that is the frontend's job.
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from bottles.backend.models.result import Result
from bottles.backend.steps.grammar import (
    ArchiveExtract,
    CabExtract,
    CopyFile,
    DeleteDlls,
    DownloadArchive,
    GetFromCab,
    InstallerRunFile,
    InstallFile,
    InstallFonts,
    LegacyCheckFileExist,
    LegacySetWindows,
    OverrideDll,
    Program,
    RegisterDll,
    RegisterFont,
    ReplaceFont,
    RunScript,
    RunWinecommand,
    SetRegisterKey,
    SetWindows,
    Uninstall,
    UpdateConfig,
    UseWindows,
)


class Executor(Protocol):
    """One method per opcode, named by Op.opcode. An executor that omits one
    fails type checking inside this package (mypy.ini re-enables the codes
    for bottles.backend.steps) and the runtime conformance test."""

    # dependency opcodes
    def download_archive(self, op: DownloadArchive) -> Result: ...
    def install_file(self, op: InstallFile) -> Result: ...
    def archive_extract(self, op: ArchiveExtract) -> Result: ...
    def cab_extract(self, op: CabExtract) -> Result: ...
    def get_from_cab(self, op: GetFromCab) -> Result: ...
    def copy_file(self, op: CopyFile) -> Result: ...
    def delete_dlls(self, op: DeleteDlls) -> Result: ...
    def install_fonts(self, op: InstallFonts) -> Result: ...
    def register_dll(self, op: RegisterDll) -> Result: ...
    def override_dll(self, op: OverrideDll) -> Result: ...
    def set_register_key(self, op: SetRegisterKey) -> Result: ...
    def register_font(self, op: RegisterFont) -> Result: ...
    def replace_font(self, op: ReplaceFont) -> Result: ...
    def set_windows(self, op: SetWindows) -> Result: ...
    def use_windows(self, op: UseWindows) -> Result: ...
    def uninstall(self, op: Uninstall) -> Result: ...

    # installer opcodes
    def installer_run_file(self, op: InstallerRunFile) -> Result: ...
    def run_script(self, op: RunScript) -> Result: ...
    def run_winecommand(self, op: RunWinecommand) -> Result: ...
    def update_config(self, op: UpdateConfig) -> Result: ...
    def legacy_set_windows(self, op: LegacySetWindows) -> Result: ...
    def legacy_check_file_exist(self, op: LegacyCheckFileExist) -> Result: ...


@dataclass
class Callbacks:
    # the dependency loop reported each step's description and reset the
    # fraction before running it; the installer loop called step_fn first
    progress: Optional[Callable[[str], None]] = None
    fraction: Optional[Callable[[Optional[float]], None]] = None
    step: Optional[Callable[[], None]] = None


def run(
    program: Program,
    executor: Executor,
    arch: str,
    callbacks: Optional[Callbacks] = None,
) -> Result:
    """Run every opcode that applies to `arch`, in order.

    Returns the executor's failing Result unchanged when a halting opcode
    fails; otherwise Result(True) with data {"uninstaller": flag}, the flag
    being False once any opcode that does not keep it has run."""
    callbacks = callbacks or Callbacks()
    uninstaller = True
    for op in program.ops:
        if not op.applies_to(arch):
            continue
        if callbacks.step:
            callbacks.step()
        if callbacks.progress:
            callbacks.progress(op.describe())
        if callbacks.fraction:
            callbacks.fraction(None)
        result = getattr(executor, op.opcode)(op)
        if op.halts_on_failure and not result.ok:
            return result
        if not op.keeps_uninstaller:
            uninstaller = False
    return Result(True, data={"uninstaller": uninstaller})
