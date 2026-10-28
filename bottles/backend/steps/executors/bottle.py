# The executor that runs manifest programs against a bottle.
#
# Each method is the handler that used to live in managers/dependency.py or
# managers/installer.py, moved as it was: the only edits are `step.get("x")`
# becoming `op.x`, the manager reference, and the return value becoming a
# Result. Every method still reports its own verdict; for the opcodes whose
# failure never stopped the old loop, the machine ignores it, as the old
# dispatcher did.
import os
import shutil
import tempfile
import traceback
from gettext import gettext as _
from glob import glob
from typing import TYPE_CHECKING, Callable, Optional

import patoolib  # type: ignore [import-untyped]

from bottles.backend.cabextract import CabExtract
from bottles.backend.globals import Paths
from bottles.backend.logger import Logger
from bottles.backend.models.config import BottleConfig
from bottles.backend.models.enum import Arch
from bottles.backend.models.result import Result
from bottles.backend.state import Status, Task
from bottles.backend.steps.grammar import (
    ArchiveExtract,
    CabExtract as CabExtractOp,
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
from bottles.backend.utils.generic import validate_url
from bottles.backend.utils.manager import ManagerUtils
from bottles.backend.wine.executor import WineExecutor
from bottles.backend.wine.reg import Reg, RegItem
from bottles.backend.wine.regkeys import RegKeys
from bottles.backend.wine.regsvr32 import Regsvr32
from bottles.backend.wine.uninstaller import Uninstaller
from bottles.backend.wine.winedbg import WineDbg
from bottles.backend.wine.winepath import WinePath

if TYPE_CHECKING:
    from bottles.backend.steps.machine import Executor

logging = Logger()

# Non-zero exit codes that still mean the installer did its job: 1638 (another
# version is already installed), 1641, 3010 and 3011 (done, restart needed).
# A step can accept more through the optional `success_codes` list.
INSTALLER_SUCCESS_CODES = (1638, 1641, 3010, 3011)


def build_progress_handler(
    task: Optional[Task],
    progress_cb: Optional[Callable[[Optional[float]], None]],
) -> Callable[[int, int, Optional[Status]], None]:
    def handler(
        received_size: int = 0,
        total_size: int = 0,
        status: Optional[Status] = None,
    ) -> None:
        if task is not None:
            task.stream_update(received_size, total_size, status)

        if progress_cb is None:
            return

        if status in [Status.DONE, Status.FAILED, Status.CANCELLED]:
            progress_cb(None)
            return

        if total_size:
            progress_cb(received_size / total_size)

    return handler


def get_real_dest(config: BottleConfig, dest: str) -> str | bool:
    """This function return the real destination path."""
    bottle = ManagerUtils.get_bottle_path(config)
    _dest = dest

    if dest.startswith("temp/"):
        dest = dest.replace("temp/", f"{Paths.temp}/")
    elif dest.startswith("windows/"):
        dest = f"{bottle}/drive_c/{dest}"
    elif dest.startswith("win32"):
        dest = f"{bottle}/drive_c/windows/system32/"
        if config.Arch == Arch.WIN64:
            dest = f"{bottle}/drive_c/windows/syswow64/"
        dest = _dest.replace("win32", dest)
    elif dest.startswith("win64"):
        if config.Arch == Arch.WIN64:
            dest = f"{bottle}/drive_c/windows/system32/"
            dest = _dest.replace("win64", dest)
        else:
            return True
    else:
        logging.error("Destination path not supported!")
        return False

    return dest


class BottleStepExecutor:
    def __init__(
        self,
        manager,
        config: BottleConfig,
        *,
        task: Optional[Task] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
        progress_fraction_cb: Optional[Callable[[Optional[float]], None]] = None,
        step_fn: Optional[Callable[[], None]] = None,
        progress_fn: Optional[Callable[[Optional[float]], None]] = None,
        activity_fn: Optional[Callable[[Optional[str]], None]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        local_resources: Optional[dict] = None,
    ):
        self.manager = manager
        self.config = config
        # dependency programs report through task/progress_cb/progress_fraction_cb
        self.task = task
        self.progress_cb = progress_cb
        self.progress_fraction_cb = progress_fraction_cb
        # installer programs report through step_fn/progress_fn/activity_fn/log_fn
        self.step_fn = step_fn
        self.progress_fn = progress_fn
        self.activity_fn = activity_fn
        self.log_fn = log_fn
        self.local_resources = local_resources if local_resources is not None else {}

    # --- dependency opcodes ---------------------------------------------

    def download_archive(self, op: DownloadArchive) -> Result:
        """
        This function download an archive from the given step.
        Can be used for any file type (cab, zip, ...). Please don't
        use this method for exe/msi files as the install_exe already
        download the exe/msi file before installation.
        """
        progress_handler = build_progress_handler(self.task, self.progress_fraction_cb)
        download = self.manager.component_manager.download(
            download_url=op.url,
            file=op.file_name,
            rename=op.rename,
            checksum=op.file_checksum,
            func=progress_handler,
            task=self.task,
        )

        return Result(download.ok)

    def install_file(self, op: InstallFile) -> Result:
        """
        Download and install the .exe or .msi file
        declared in the step, in a bottle. The step fails when the
        installer exits with a status that is not a success code.
        """
        config = self.config
        winedbg = WineDbg(config)
        progress_handler = build_progress_handler(self.task, self.progress_fraction_cb)
        download = self.manager.component_manager.download(
            download_url=op.url,
            file=op.file_name,
            rename=op.rename,
            checksum=op.file_checksum,
            func=progress_handler,
            task=self.task,
        )
        file = op.file_name
        if op.rename:
            file = op.rename

        if download.ok:
            if op.url.startswith("temp/"):
                _file = op.url.replace("temp/", f"{Paths.temp}/")
                file = f"{_file}/{file}"
            else:
                file = f"{Paths.temp}/{file}"
            extra_codes = op.success_codes
            success_codes = list(INSTALLER_SUCCESS_CODES)
            if isinstance(extra_codes, (list, tuple)):
                success_codes += [c for c in extra_codes if type(c) is int]
            staged = None
            cwd = None
            try:
                prefix = ManagerUtils.get_bottle_path(config)
                if not os.path.exists(os.path.join(prefix, "dosdevices", "z:")):
                    temp_dir = os.path.join(prefix, "drive_c", "windows", "temp")
                    os.makedirs(temp_dir, exist_ok=True)
                    staged = tempfile.TemporaryDirectory(prefix="bottles-installer-", dir=temp_dir)
                    cwd = staged.name
                    destination = os.path.join(cwd, os.path.basename(file))
                    if op.url.startswith("temp/"):
                        shutil.copytree(os.path.dirname(file), cwd, dirs_exist_ok=True)
                    else:
                        shutil.copy2(file, destination)
                    file = WinePath(config).to_windows(destination, native=True)

                executor = WineExecutor(
                    config,
                    exec_path=file,
                    args=op.arguments,
                    environment=op.environment,
                    success_codes=success_codes,
                    cwd=cwd,
                )
                result = executor.run()
                winedbg.wait_for_process(os.path.basename(file.replace("\\", "/")))
            except OSError as error:
                return Result(False, message=f"Could not prepare installer: {error}")
            finally:
                if staged:
                    staged.cleanup()

            if not result.ok:
                message = result.message or f"Failed to run {op.file_name}."
                logging.error(message)
                return Result(status=False, message=message)

            if self.progress_fraction_cb:
                self.progress_fraction_cb(None)

            if self.progress_cb:
                self.progress_cb(_("Installer completed."))
            return Result(status=True)

        return Result(
            status=False,
            message=f"Failed to download {op.file_name}, or checksum failed.",
        )

    def uninstall(self, op: Uninstall) -> Result:
        """
        This function find an uninstaller in the bottle by the given
        file name and execute it.
        """
        Uninstaller(self.config).from_name(op.file_name)
        return Result(True)

    def cab_extract(self, op: CabExtractOp) -> Result:
        """
        This function download and extract a Windows Cabinet to the
        temp folder.
        """
        dest = op.dest
        if dest.startswith("temp/"):
            dest = dest.replace("temp/", f"{Paths.temp}/")
        else:
            logging.error("Destination path not supported!")
            return Result(False)

        if validate_url(op.url):
            progress_handler = build_progress_handler(self.task, self.progress_fraction_cb)
            download = self.manager.component_manager.download(
                download_url=op.url,
                file=op.file_name,
                rename=op.rename,
                checksum=op.file_checksum,
                func=progress_handler,
                task=self.task,
            )

            if download.ok:
                if op.rename:
                    file = op.rename
                else:
                    file = op.file_name

                if not CabExtract().run(
                    path=os.path.join(Paths.temp, file), name=file, destination=dest
                ):
                    return Result(False)
            else:
                return Result(False)

        elif op.url.startswith("temp/"):
            path = op.url
            path = path.replace("temp/", f"{Paths.temp}/")

            if op.rename:
                file_path = os.path.splitext(f"{op.rename}")[0]
            else:
                file_path = os.path.splitext(f"{op.file_name}")[0]

            if not CabExtract().run(
                f"{path}/{op.file_name}", file_path, destination=dest
            ):
                return Result(False)

        return Result(True)

    def delete_dlls(self, op: DeleteDlls) -> Result:
        """Deletes the given dlls from the system32 or syswow64 paths"""
        dest = get_real_dest(self.config, op.dest)

        for d in op.dlls:
            _d = os.path.join(dest, d)
            if os.path.exists(_d):
                os.remove(_d)

        return Result(True)

    def get_from_cab(self, op: GetFromCab) -> Result:
        """Take a file from a cabiner and extract to a path."""
        source = op.source
        file_name = op.file_name
        rename = op.rename
        dest = get_real_dest(self.config, op.dest)

        if isinstance(dest, bool):
            return Result(dest)

        res = CabExtract().run(
            path=os.path.join(Paths.temp, source), files=[file_name], destination=dest
        )

        if rename:
            _file_name = file_name.split("/")[-1]
            _file_name_path = os.path.join(dest, _file_name)

            if not os.path.exists(_file_name_path):
                files = os.listdir(dest)
                for f in files:
                    if f.lower() == _file_name.lower():
                        _file_name = f
                        _file_name_path = os.path.join(dest, _file_name)
                        break

            if not os.path.exists(_file_name_path):
                logging.error(f"Cannot find extracted file: {_file_name_path}")
                return Result(False)

            rename_path = os.path.join(dest, rename)
            if os.path.normpath(_file_name_path) != os.path.normpath(rename_path):
                if os.path.exists(rename_path):
                    os.remove(rename_path)

                shutil.move(_file_name_path, rename_path)

        if not res:
            return Result(False)
        return Result(True)

    def archive_extract(self, op: ArchiveExtract) -> Result:
        """Download and extract an archive to the temp folder."""
        progress_handler = build_progress_handler(self.task, self.progress_fraction_cb)
        download = self.manager.component_manager.download(
            download_url=op.url,
            file=op.file_name,
            rename=op.rename,
            checksum=op.file_checksum,
            func=progress_handler,
            task=self.task,
        )

        if not download.ok:
            return Result(False)

        if op.rename:
            file = op.rename
        else:
            file = op.file_name

        archive_path = os.path.join(Paths.temp, os.path.splitext(file)[0])

        if os.path.exists(archive_path):
            shutil.rmtree(archive_path)

        os.makedirs(archive_path)
        try:
            patoolib.extract_archive(
                os.path.join(Paths.temp, file), outdir=archive_path
            )
            if archive_path.endswith(".tar") and os.path.isfile(
                os.path.join(archive_path, os.path.basename(archive_path))
            ):
                tar_path = os.path.join(archive_path, os.path.basename(archive_path))
                patoolib.extract_archive(tar_path, outdir=archive_path)
        except Exception as e:
            logging.error("Something wrong happened during extraction.")
            logging.error(f"{e}")
            logging.error(f"{traceback.format_exc()}")
            return Result(False)
        return Result(True)

    def install_fonts(self, op: InstallFonts) -> Result:
        """Move fonts to the drive_c/windows/Fonts path."""
        path = op.url
        path = path.replace("temp/", f"{Paths.temp}/")
        bottle_path = ManagerUtils.get_bottle_path(self.config)

        for font in op.fonts:
            font_path = f"{bottle_path}/drive_c/windows/Fonts/"
            if not os.path.exists(font_path):
                os.makedirs(font_path)

            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=font_path, delete=False) as target:
                    temporary = target.name
                shutil.copyfile(f"{path}/{font}", temporary)
                os.replace(temporary, f"{font_path}/{font}")
            except OSError as error:
                logging.warning(f"Could not install font {font}: {error}")
                return Result(False)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)

        return Result(True)

    # noinspection PyTypeChecker
    def copy_file(self, op: CopyFile) -> Result:
        """
        This function copy dlls from temp folder to a directory
        declared in the step. The bottle drive_c path will be used as
        root path.
        """
        path = op.url
        path = path.replace("temp/", f"{Paths.temp}/")
        dest = get_real_dest(self.config, op.dest)

        if isinstance(dest, bool):
            return Result(dest)

        if not os.path.exists(dest):
            os.makedirs(dest)

        try:
            if "*" in op.file_name:
                files = glob(f"{path}/{op.file_name}")
                if not files:
                    logging.info(f"File(s) not found in {path}")
                    return Result(False)
                for fg in files:
                    _name = fg.split("/")[-1]
                    _path = os.path.join(path, _name)
                    _dest = os.path.join(dest, _name)
                    logging.info(f"Copying {_name} to {_dest}")

                    if os.path.exists(_dest) and os.path.islink(_dest):
                        os.unlink(_dest)

                    try:
                        shutil.copyfile(_path, _dest)
                    except shutil.SameFileError:
                        logging.info(
                            f"{_name} already exists at the same version, skipping."
                        )
            else:
                _name = op.file_name
                _dest = os.path.join(dest, _name)
                logging.info(f"Copying {_name} to {_dest}")

                if os.path.exists(_dest) and os.path.islink(_dest):
                    os.unlink(_dest)

                try:
                    shutil.copyfile(os.path.join(path, _name), _dest)
                except shutil.SameFileError:
                    logging.info(
                        f"{_name} already exists at the same version, skipping."
                    )

        except Exception as e:
            print(e)
            logging.warning("An error occurred while copying dlls.")
            return Result(False)

        return Result(True)

    def register_dll(self, op: RegisterDll) -> Result:
        """Register one or more dll and ActiveX control"""
        regsvr32 = Regsvr32(self.config)

        for dll in op.dlls:
            regsvr32.register(dll)

        return Result(True)

    def override_dll(self, op: OverrideDll) -> Result:
        """Register a new override for each dll."""
        reg = Reg(self.config)

        if op.url and op.url.startswith("temp/"):
            path = op.url.replace("temp/", f"{Paths.temp}/")
            dlls = glob(os.path.join(path, op.dll))

            bundle = {"HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides": []}

            for dll in dlls:
                dll_name = os.path.splitext(os.path.basename(dll))[0]
                bundle["HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides"].append(
                    {"value": dll_name, "data": op.type}
                )

            reg.import_bundle(bundle)
            return Result(True)

        if op.bundle:
            _bundle = {
                "HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides": list(op.bundle)
            }
            reg.import_bundle(_bundle)
            return Result(True)

        reg.add(
            key="HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides",
            value=op.dll,
            data=op.type,
        )
        return Result(True)

    def set_register_key(self, op: SetRegisterKey) -> Result:
        """Set a registry key."""
        reg = Reg(self.config)
        reg.add(
            key=op.key,
            value=op.value,
            data=op.data,
            value_type=op.type,
        )
        return Result(True)

    def register_font(self, op: RegisterFont) -> Result:
        """Register a font in the registry."""
        reg = Reg(self.config)
        reg.add(
            key="HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts",
            value=op.name,
            data=op.file,
        )
        return Result(True)

    def replace_font(self, op: ReplaceFont) -> Result:
        """Register a font replacement in the registry."""
        reg = Reg(self.config)
        target_font = op.font
        replaces = op.replace

        if not isinstance(replaces, (list, tuple)):
            logging.warning("Invalid replace_font, 'replace' field should be list.")
            return Result(False)

        regs = [
            RegItem(
                key="HKEY_CURRENT_USER\\Software\\Wine\\Fonts\\Replacements",
                value=r,
                value_type="",
                data=target_font,
            )
            for r in replaces
        ]
        reg.bulk_add(regs)
        return Result(True)

    def set_windows(self, op: SetWindows) -> Result:
        """Set the Windows version."""
        rk = RegKeys(self.config)
        try:
            rk.lg_set_windows(op.version)
        except ValueError as e:
            logging.warning(str(e))
        return Result(True)

    def use_windows(self, op: UseWindows) -> Result:
        """Set a Windows version per program."""
        rk = RegKeys(self.config)
        rk.set_app_default(op.version, op.executable)
        return Result(True)

    # --- installer opcodes (moved in the next step) -----------------------

    def installer_run_file(self, op: InstallerRunFile) -> Result:
        raise NotImplementedError

    def run_script(self, op: RunScript) -> Result:
        raise NotImplementedError

    def run_winecommand(self, op: RunWinecommand) -> Result:
        raise NotImplementedError

    def update_config(self, op: UpdateConfig) -> Result:
        raise NotImplementedError

    def legacy_set_windows(self, op: LegacySetWindows) -> Result:
        raise NotImplementedError

    def legacy_check_file_exist(self, op: LegacyCheckFileExist) -> Result:
        raise NotImplementedError


if TYPE_CHECKING:
    _conforms: "Executor" = BottleStepExecutor(None, BottleConfig())
