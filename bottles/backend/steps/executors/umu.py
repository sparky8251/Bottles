# The executor that runs dependency programs for a umu game.
#
# umu accepts only install_exe and override_dll steps (the frontend's
# check_umu_target refuses everything else before a program gets here), runs
# installers through umu-run against the game record and accumulates DLL
# overrides into the game's environment instead of the bottle registry. The
# bodies are UmuDependencyInstaller._install_executable and _apply_override,
# moved as they were.
import shlex
from dataclasses import replace
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from bottles.backend.globals import Paths
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
    Op,
    OverrideDll,
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

if TYPE_CHECKING:
    from bottles.backend.steps.machine import Executor


class UmuDependencyError(RuntimeError):
    pass


def arguments_of(value) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    values = value if isinstance(value, (list, tuple)) else [value]
    arguments = []
    for item in values:
        if not isinstance(item, str):
            raise UmuDependencyError("Invalid dependency arguments")
        arguments.extend(shlex.split(item))
    return tuple(arguments)


class UmuStepExecutor:
    def __init__(
        self,
        manager,
        game,
        process_executor,
        environment: dict,
        progress_cb: Optional[Callable[[str], None]] = None,
        progress_progress_cb: Optional[Callable[[Optional[float]], None]] = None,
    ):
        self.manager = manager
        self.game = game
        self.process_executor = process_executor
        # shared with the caller: overrides accumulate here across programs
        self.environment = environment
        self.progress_cb = progress_cb
        self.progress_progress_cb = progress_progress_cb

    def install_file(self, op: InstallFile) -> Result:
        progress_cb = self.progress_cb
        progress_progress_cb = self.progress_progress_cb
        url = op.url
        file_name = op.file_name
        rename = op.rename or ""
        if not isinstance(url, str) or not isinstance(file_name, str):
            raise UmuDependencyError("Invalid dependency download")
        if url.startswith("temp/"):
            raise UmuDependencyError("Temporary dependency sources are not supported")

        if progress_cb:
            progress_cb(_("Downloading {0}...").format(rename or file_name))

        def update_progress(received=0, total=0, _status=None):
            if progress_progress_cb:
                progress_progress_cb(received / total if total else None)

        result = self.manager.component_manager.download(
            download_url=url,
            file=file_name,
            rename=rename,
            checksum=op.file_checksum or "",
            func=update_progress,
        )
        if progress_progress_cb:
            progress_progress_cb(None)
        if not result.ok:
            raise UmuDependencyError(f"Cannot download {file_name}")

        executable = Path(Paths.temp) / (rename or file_name)
        if not executable.is_file():
            raise UmuDependencyError(
                f"Downloaded dependency is missing: {executable.name}"
            )

        step_environment = op.environment if op.environment is not None else {}
        if not isinstance(step_environment, dict):
            raise UmuDependencyError("Invalid dependency environment")

        game = replace(self.game, environment=self.environment)
        installer = replace(
            game,
            executable=executable,
            arguments=arguments_of(op.arguments),
            working_directory=Path(Paths.temp),
            environment={**game.environment, **step_environment},
        )
        if progress_cb:
            progress_cb(_("Running {0}...").format(executable.name))
        self.process_executor.run(installer)
        return_code = self.process_executor.wait(installer)
        if return_code not in (0, 194):
            raise UmuDependencyError(
                f"Dependency installer exited with status {return_code}"
            )
        return Result(True)

    def override_dll(self, op: OverrideDll) -> Result:
        if self.progress_cb:
            self.progress_cb(_("Updating DLL overrides..."))
        environment = self.environment
        overrides = []
        if op.bundle:
            for item in op.bundle:
                if not isinstance(item, dict):
                    raise UmuDependencyError("Invalid DLL override")
                overrides.append((item.get("value"), item.get("data")))
        else:
            overrides.append((op.dll, op.type))

        value = environment.get("WINEDLLOVERRIDES", "").strip(";")
        parts = [value] if value else []
        for name, mode in overrides:
            if not isinstance(name, str) or not isinstance(mode, str):
                raise UmuDependencyError("Invalid DLL override")
            parts.append(f"{name}={mode}")
        environment["WINEDLLOVERRIDES"] = ";".join(parts)
        return Result(True)

    # every other opcode is refused by check_umu_target before a program runs

    def _unreachable(self, op: Op) -> Result:
        raise AssertionError(f"unreachable: {op.opcode} is rejected by check_umu_target")

    def download_archive(self, op: DownloadArchive) -> Result:
        return self._unreachable(op)

    def archive_extract(self, op: ArchiveExtract) -> Result:
        return self._unreachable(op)

    def cab_extract(self, op: CabExtract) -> Result:
        return self._unreachable(op)

    def get_from_cab(self, op: GetFromCab) -> Result:
        return self._unreachable(op)

    def copy_file(self, op: CopyFile) -> Result:
        return self._unreachable(op)

    def delete_dlls(self, op: DeleteDlls) -> Result:
        return self._unreachable(op)

    def install_fonts(self, op: InstallFonts) -> Result:
        return self._unreachable(op)

    def register_dll(self, op: RegisterDll) -> Result:
        return self._unreachable(op)

    def set_register_key(self, op: SetRegisterKey) -> Result:
        return self._unreachable(op)

    def register_font(self, op: RegisterFont) -> Result:
        return self._unreachable(op)

    def replace_font(self, op: ReplaceFont) -> Result:
        return self._unreachable(op)

    def set_windows(self, op: SetWindows) -> Result:
        return self._unreachable(op)

    def use_windows(self, op: UseWindows) -> Result:
        return self._unreachable(op)

    def uninstall(self, op: Uninstall) -> Result:
        return self._unreachable(op)

    def installer_run_file(self, op: InstallerRunFile) -> Result:
        return self._unreachable(op)

    def run_script(self, op: RunScript) -> Result:
        return self._unreachable(op)

    def run_winecommand(self, op: RunWinecommand) -> Result:
        return self._unreachable(op)

    def update_config(self, op: UpdateConfig) -> Result:
        return self._unreachable(op)

    def legacy_set_windows(self, op: LegacySetWindows) -> Result:
        return self._unreachable(op)

    def legacy_check_file_exist(self, op: LegacyCheckFileExist) -> Result:
        return self._unreachable(op)


if TYPE_CHECKING:
    _conforms: "Executor" = UmuStepExecutor(None, None, None, {})
