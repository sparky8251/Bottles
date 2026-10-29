# The instruction set that dependency and installer manifests compile to.
#
# A program is a header plus a flat tuple of opcodes. There are no jumps,
# no variables and no state between opcodes: the machine runs them in order
# and the executor does the side effects. This module imports only the
# standard library so `python -m bottles.backend.steps` can parse a manifest
# on a host with no GTK, no Wine and no bottle.
import json
import typing
from dataclasses import dataclass, fields as dataclass_fields
from enum import Enum
from gettext import gettext as _
from typing import ClassVar, Optional, Union

ARCH_BOTH = "win64_win32"


class ProgramKind(Enum):
    DEPENDENCY = "dependency"
    INSTALLER = "installer"


@dataclass(frozen=True)
class Op:
    """One instruction. Subclasses add fields named after the manifest keys."""

    opcode: ClassVar[str]
    spellings: ClassVar[tuple[str, ...]]
    kind: ClassVar[ProgramKind]
    # the old dispatcher cleared the "uninstaller" flag after extract-style
    # steps and ignored the return value of registry-style steps; both facts
    # are properties of the opcode, so they live here
    keeps_uninstaller: ClassVar[bool] = True
    halts_on_failure: ClassVar[bool] = False
    # manifest key -> whether the frontend turns a YAML list into a tuple.
    # Manifest keys and Op attributes are two namespaces: the keys an opcode
    # accepts are exactly the fields it declares itself (filled in by
    # _collect from the dataclass), never anything inherited from Op, so an
    # attribute added here can never become a reserved word in a manifest.
    manifest_fields: ClassVar[dict[str, bool]] = {}

    arches: Union[str, tuple[str, ...]] = ARCH_BOTH
    # the action name as the manifest wrote it; synonyms share an opcode but
    # messages quote the author's spelling
    spelling: str = ""

    def __post_init__(self):
        if not self.spelling and self.spellings:
            object.__setattr__(self, "spelling", self.spellings[0])

    def applies_to(self, arch: str) -> bool:
        # substring on a string, membership on a tuple: exactly the old
        # `config.Arch not in step.get("for", "win64_win32")`
        return arch in self.arches

    def describe(self) -> str:
        return _("Running {0}...").format(self.opcode.replace("_", " "))

    def fields(self) -> dict[str, object]:
        return {
            f.name: getattr(self, f.name)
            for f in sorted(dataclass_fields(self), key=lambda f: f.name)
            if f.name not in ("arches", "spelling") and getattr(self, f.name) is not None
        }

    def dump(self) -> str:
        parts = [self.opcode]
        if self.arches != ARCH_BOTH:
            parts.append(f"for={_dump_value(self.arches)}")
        parts.extend(f"{key}={_dump_value(value)}" for key, value in self.fields().items())
        return " ".join(parts)


def _dump_value(value: object) -> str:
    if isinstance(value, str) and value and not any(c.isspace() for c in value):
        return value
    if isinstance(value, tuple):
        value = list(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _file_label(op: "Op", fallback: str) -> str:
    name = getattr(op, "rename", None) or getattr(op, "file_name", None)
    return name or fallback


# --- dependency opcodes -------------------------------------------------


@dataclass(frozen=True)
class DownloadArchive(Op):
    opcode = "download_archive"
    spellings = ("download_archive",)
    kind = ProgramKind.DEPENDENCY
    halts_on_failure = True

    url: Optional[str] = None
    file_name: Optional[str] = None
    rename: Optional[str] = None
    file_checksum: Optional[str] = None
    file_size: Optional[int] = None

    def describe(self) -> str:
        return _("Downloading {0}...").format(_file_label(self, _("archive")))


@dataclass(frozen=True)
class InstallFile(Op):
    opcode = "install_file"
    spellings = ("install_exe", "install_msi")
    kind = ProgramKind.DEPENDENCY
    halts_on_failure = True

    url: Optional[str] = None
    file_name: Optional[str] = None
    rename: Optional[str] = None
    file_checksum: Optional[str] = None
    file_size: Optional[int] = None
    # forwarded as written (umu splits it; the bottle executor passes it on)
    arguments: Union[str, list, None] = None
    environment: Optional[dict] = None
    success_codes: Optional[tuple[int, ...]] = None

    def describe(self) -> str:
        return _("Running {0}…").format(_file_label(self, _("installer")))


@dataclass(frozen=True)
class ArchiveExtract(Op):
    opcode = "archive_extract"
    spellings = ("archive_extract",)
    kind = ProgramKind.DEPENDENCY
    keeps_uninstaller = False
    halts_on_failure = True

    url: Optional[str] = None
    file_name: Optional[str] = None
    rename: Optional[str] = None
    file_checksum: Optional[str] = None
    file_size: Optional[int] = None
    dest: Optional[str] = None

    def describe(self) -> str:
        return _("Unpacking archive…")


@dataclass(frozen=True)
class CabExtract(Op):
    opcode = "cab_extract"
    spellings = ("cab_extract",)
    kind = ProgramKind.DEPENDENCY
    keeps_uninstaller = False
    halts_on_failure = True

    url: Optional[str] = None
    file_name: Optional[str] = None
    rename: Optional[str] = None
    file_checksum: Optional[str] = None
    file_size: Optional[int] = None
    dest: Optional[str] = None

    def describe(self) -> str:
        return _("Extracting {0}…").format(_file_label(self, _("package")))


@dataclass(frozen=True)
class GetFromCab(Op):
    opcode = "get_from_cab"
    spellings = ("get_from_cab",)
    kind = ProgramKind.DEPENDENCY
    keeps_uninstaller = False
    halts_on_failure = True

    source: Optional[str] = None
    file_name: Optional[str] = None
    rename: Optional[str] = None
    dest: Optional[str] = None

    def describe(self) -> str:
        return _("Extracting {0} from cabinet…").format(_file_label(self, _("files")))


@dataclass(frozen=True)
class CopyFile(Op):
    opcode = "copy_file"
    spellings = ("copy_dll", "copy_file")
    kind = ProgramKind.DEPENDENCY
    keeps_uninstaller = False
    halts_on_failure = True

    url: Optional[str] = None
    file_name: Optional[str] = None
    dest: Optional[str] = None

    def describe(self) -> str:
        return _("Copying files…")


@dataclass(frozen=True)
class DeleteDlls(Op):
    opcode = "delete_dlls"
    spellings = ("delete_dlls",)
    kind = ProgramKind.DEPENDENCY

    dest: Optional[str] = None
    dlls: tuple[str, ...] = ()

    def describe(self) -> str:
        return _("Cleaning previous files…")


@dataclass(frozen=True)
class InstallFonts(Op):
    opcode = "install_fonts"
    spellings = ("install_fonts", "install_cab_fonts")
    kind = ProgramKind.DEPENDENCY
    keeps_uninstaller = False
    halts_on_failure = True

    url: Optional[str] = None
    fonts: Optional[tuple[str, ...]] = None

    def describe(self) -> str:
        return _("Installing fonts…")


@dataclass(frozen=True)
class RegisterDll(Op):
    opcode = "register_dll"
    spellings = ("register_dll",)
    kind = ProgramKind.DEPENDENCY

    dlls: tuple[str, ...] = ()

    def describe(self) -> str:
        return _("Registering libraries…")


@dataclass(frozen=True)
class OverrideDll(Op):
    opcode = "override_dll"
    spellings = ("override_dll",)
    kind = ProgramKind.DEPENDENCY

    dll: Optional[str] = None
    type: Optional[str] = None
    bundle: Optional[tuple[dict, ...]] = None
    url: Optional[str] = None

    def describe(self) -> str:
        return _("Updating DLL overrides…")


@dataclass(frozen=True)
class SetRegisterKey(Op):
    opcode = "set_register_key"
    spellings = ("set_register_key",)
    kind = ProgramKind.DEPENDENCY

    key: Optional[str] = None
    value: Optional[str] = None
    data: Optional[str] = None
    type: Optional[str] = None

    def describe(self) -> str:
        return _("Writing registry keys…")


@dataclass(frozen=True)
class RegisterFont(Op):
    opcode = "register_font"
    spellings = ("register_font",)
    kind = ProgramKind.DEPENDENCY

    name: Optional[str] = None
    file: Optional[str] = None

    def describe(self) -> str:
        return _("Registering fonts…")


@dataclass(frozen=True)
class ReplaceFont(Op):
    opcode = "replace_font"
    spellings = ("replace_font",)
    kind = ProgramKind.DEPENDENCY

    font: Optional[str] = None
    replace: Optional[tuple[str, ...]] = None

    def describe(self) -> str:
        return _("Replacing fonts…")


@dataclass(frozen=True)
class SetWindows(Op):
    opcode = "set_windows"
    spellings = ("set_windows",)
    kind = ProgramKind.DEPENDENCY

    version: Optional[str] = None
    # one live manifest (dotnet472) writes an executable on set_windows; the
    # handler has always ignored it and the grammar keeps the field as written
    executable: Optional[str] = None

    def describe(self) -> str:
        return _("Adjusting Windows version…")


@dataclass(frozen=True)
class UseWindows(Op):
    opcode = "use_windows"
    spellings = ("use_windows",)
    kind = ProgramKind.DEPENDENCY

    version: Optional[str] = None
    executable: Optional[str] = None

    def describe(self) -> str:
        return _("Applying Windows version…")


@dataclass(frozen=True)
class Uninstall(Op):
    opcode = "uninstall"
    spellings = ("uninstall",)
    kind = ProgramKind.DEPENDENCY

    file_name: Optional[str] = None

    def describe(self) -> str:
        return _("Running uninstaller…")


# --- installer opcodes --------------------------------------------------


@dataclass(frozen=True)
class InstallerRunFile(Op):
    opcode = "installer_run_file"
    spellings = ("install_exe", "install_msi")
    kind = ProgramKind.INSTALLER
    halts_on_failure = True

    url: Optional[str] = None
    file_name: Optional[str] = None
    rename: Optional[str] = None
    file_checksum: Union[str, bool, None] = None
    # forwarded as written (umu splits it; the bottle executor passes it on)
    arguments: Union[str, list, None] = None
    environment: Optional[dict] = None
    monitoring: Optional[tuple[str, ...]] = None

    def describe(self) -> str:
        return _("Running {0}…").format(_file_label(self, _("installer")))


@dataclass(frozen=True)
class RunScript(Op):
    opcode = "run_script"
    spellings = ("run_script",)
    kind = ProgramKind.INSTALLER
    halts_on_failure = True

    script: Optional[str] = None

    def describe(self) -> str:
        return "installer script"


@dataclass(frozen=True)
class RunWinecommand(Op):
    opcode = "run_winecommand"
    spellings = ("run_winecommand",)
    kind = ProgramKind.INSTALLER
    halts_on_failure = True

    commands: tuple[dict, ...] = ()

    def describe(self) -> str:
        return _("Running Wine commands…")


@dataclass(frozen=True)
class UpdateConfig(Op):
    opcode = "update_config"
    spellings = ("update_config",)
    kind = ProgramKind.INSTALLER

    path: Optional[str] = None
    type: Optional[str] = None
    del_keys: Optional[dict] = None
    upd_keys: Optional[dict] = None

    def describe(self) -> str:
        return _("Updating configuration…")


@dataclass(frozen=True)
class LegacySetWindows(Op):
    # starcitizen.yml carries a set_windows step the installer code never
    # handled; it stays a no-op so that installer keeps working
    opcode = "legacy_set_windows"
    spellings = ("set_windows",)
    kind = ProgramKind.INSTALLER

    version: Optional[str] = None

    def describe(self) -> str:
        return "Skipping set_windows"


@dataclass(frozen=True)
class LegacyCheckFileExist(Op):
    # itchio.yml carries a check_file_exist step no code has ever handled
    opcode = "legacy_check_file_exist"
    spellings = ("check_file_exist",)
    kind = ProgramKind.INSTALLER

    path: Optional[str] = None

    def describe(self) -> str:
        return "Skipping check_file_exist"


@dataclass(frozen=True)
class LegacyNoop(Op):
    # lax mode lowers an action no opcode defines to this: the old loops fell
    # through their if-chains and counted the step as a success. It has no
    # spelling of its own, so it is never in OPCODES; strict mode rejects
    # the action instead. It serves both program kinds; `kind` is only set
    # because Op requires one, and nothing reads it for this class.
    opcode = "legacy_noop"
    spellings = ()
    kind = ProgramKind.DEPENDENCY

    def describe(self) -> str:
        # the old dependency loop's fallback text for an action it had no
        # description for
        return _("Running {0}...").format(self.spelling.replace("_", " "))


def _is_tuple_type(annotation: object) -> bool:
    """True when the declared type is a tuple, possibly inside Optional/Union."""
    origin = typing.get_origin(annotation)
    if origin is Union:
        return any(_is_tuple_type(arg) for arg in typing.get_args(annotation))
    return origin is tuple or annotation is tuple


def _collect() -> dict[ProgramKind, dict[str, type[Op]]]:
    table: dict[ProgramKind, dict[str, type[Op]]] = {kind: {} for kind in ProgramKind}
    inherited = {f.name for f in dataclass_fields(Op)}

    def subclasses(base):
        for cls in base.__subclasses__():
            yield cls
            yield from subclasses(cls)

    for cls in subclasses(Op):
        cls.manifest_fields = {
            f.name: _is_tuple_type(f.type)
            for f in dataclass_fields(cls)
            if f.name not in inherited
        }
        for spelling in cls.spellings:
            assert spelling not in table[cls.kind], (cls.kind, spelling)
            table[cls.kind][spelling] = cls
    return table


OPCODES: dict[ProgramKind, dict[str, type[Op]]] = _collect()

# every opcode class an executor must implement: the spelled ones plus the
# lax-mode fallback
ALL_OPCODES: tuple[type[Op], ...] = tuple(
    sorted({cls for table in OPCODES.values() for cls in table.values()} | {LegacyNoop}, key=lambda c: c.opcode)
)


# --- programs -----------------------------------------------------------


@dataclass(frozen=True)
class DependencyHeader:
    name: str
    dependencies: tuple[str, ...] = ()
    uninstaller: Optional[str] = None
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallerHeader:
    name: str
    dependencies: tuple = ()
    parameters: Optional[dict] = None
    executables: tuple[dict, ...] = ()
    # True when the manifest used the plural `Executables` key: those programs
    # may be missing after install, a single `Executable` may not
    optional_executables: bool = False
    checks: Optional[dict] = None
    installers: tuple = ()


@dataclass(frozen=True)
class Program:
    kind: ProgramKind
    header: Union[DependencyHeader, InstallerHeader]
    ops: tuple[Op, ...]

    def cacheable_downloads(self, arch: Optional[str] = None) -> list[tuple[str, str]]:
        """(file, checksum) for every op that downloads something, in order.

        Steps whose url points into temp/ are skipped, as the cache check
        always did; a download without a file name yields ("", checksum) so
        the caller can refuse it the way the old loop did."""
        found = []
        for op in self.ops:
            if arch and not op.applies_to(arch):
                continue
            url = getattr(op, "url", None)
            if not url or url.startswith("temp/"):
                continue
            file = getattr(op, "rename", None) or getattr(op, "file_name", None) or ""
            found.append((file, getattr(op, "file_checksum", None) or ""))
        return found

    def dump(self) -> str:
        return "\n".join(op.dump() for op in self.ops)
