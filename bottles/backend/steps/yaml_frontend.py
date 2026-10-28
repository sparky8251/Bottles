# The YAML frontend: a manifest dict in, a Program out.
#
# This is the one place the manifest shape is checked. It accepts exactly
# what DependencyManager.install and InstallerManager.install accepted and
# raises ManifestError with the message those methods showed. It performs no
# optimization: every step becomes one opcode in manifest order with its
# fields as written, arch filtering included, and the machine decides later.
import os
from typing import Optional, TypeGuard

import yaml

from bottles.backend.steps.grammar import (
    OPCODES,
    DependencyHeader,
    InstallerHeader,
    InstallFile,
    Op,
    OverrideDll,
    Program,
    ProgramKind,
)


class ManifestError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse(manifest: object, kind: ProgramKind, name: str) -> Program:
    if not isinstance(manifest, dict):
        raise ManifestError(f"Cannot find manifest for {name}.")
    if kind is ProgramKind.DEPENDENCY:
        return _parse_dependency(manifest, name)
    return _parse_installer(manifest, name)


def parse_file(path: str, kind: ProgramKind) -> Program:
    with open(path, "rb") as handle:
        manifest = yaml.safe_load(handle)
    name = os.path.splitext(os.path.basename(path))[0]
    if isinstance(manifest, dict) and isinstance(manifest.get("Name"), str):
        name = manifest["Name"]
    return parse(manifest, kind, name)


def _is_str_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _parse_dependency(manifest: dict, name: str) -> Program:
    invalid = ManifestError(f"Invalid manifest for {name}.")
    dependencies = manifest.get("Dependencies", [])
    steps = manifest.get("Steps")
    if not _is_str_list(dependencies) or not isinstance(steps, list):
        raise invalid
    for step in steps:
        # the acceptance rules of DependencyManager.install, unchanged
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise invalid
        if not (
            isinstance(step.get("for", "win64_win32"), str) or _is_str_list(step.get("for"))
        ):
            raise invalid
        for key in ("url", "file_name", "rename", "file_checksum"):
            if not isinstance(step.get(key, ""), str):
                raise invalid

    uninstaller = manifest.get("Uninstaller")
    conflicts = manifest.get("Conflicts")
    header = DependencyHeader(
        name=name,
        dependencies=tuple(dependencies),
        uninstaller=uninstaller if isinstance(uninstaller, str) else None,
        conflicts=tuple(conflicts) if _is_str_list(conflicts) else (),
    )
    return Program(ProgramKind.DEPENDENCY, header, _lower(steps, ProgramKind.DEPENDENCY, invalid))


def _parse_installer(manifest: dict, name: str) -> Program:
    invalid = ManifestError("Installer is not well configured.")
    executables = manifest.get("Executables")
    optional_executables = executables is not None
    if executables is None:
        executables = [manifest.get("Executable")]
    if (
        not isinstance(executables, list)
        or not executables
        or any(
            not isinstance(item, dict) or not item.get("file") or not item.get("name")
            for item in executables
        )
    ):
        raise invalid
    steps = manifest.get("Steps") or []
    if not isinstance(steps, list) or any(
        not isinstance(step, dict) or not isinstance(step.get("action"), str) for step in steps
    ):
        raise invalid

    dependencies = manifest.get("Dependencies") or []
    installers = manifest.get("Installers") or []
    header = InstallerHeader(
        name=name,
        dependencies=tuple(dependencies) if _is_str_list(dependencies) else (),
        parameters=manifest.get("Parameters") or None,
        executables=tuple(executables),
        optional_executables=optional_executables,
        checks=manifest.get("Checks") or None,
        installers=tuple(installers) if isinstance(installers, list) else (),
    )
    return Program(ProgramKind.INSTALLER, header, _lower(steps, ProgramKind.INSTALLER, invalid))


def _lower(steps: list, kind: ProgramKind, invalid: ManifestError) -> tuple[Op, ...]:
    table = OPCODES[kind]
    ops = []
    for step in steps:
        cls = table.get(step["action"])
        if cls is None:
            raise invalid
        values = {"spelling": step["action"]}
        for key, value in step.items():
            if key == "action":
                continue
            if key == "for":
                values["arches"] = tuple(value) if isinstance(value, list) else value
                continue
            if key not in cls.__dataclass_fields__:
                raise invalid
            values[key] = tuple(value) if isinstance(value, list) else value
        ops.append(cls(**values))
    return tuple(ops)


def check_umu_target(program: Program) -> None:
    """Reject what UmuDependencyInstaller cannot run, with its messages."""
    name = program.header.name
    for op in program.ops:
        # umu accepted exactly the spellings install_exe and override_dll, so
        # an install_msi step is refused even though it is the same opcode
        if isinstance(op, InstallFile) and op.spelling == "install_exe":
            if not isinstance(op.url, str) or not isinstance(op.file_name, str):
                raise ManifestError(f"Bottles dependency {name} has an invalid download")
            if op.url.startswith("temp/"):
                raise ManifestError(f"Bottles dependency {name} uses a temporary source")
            if op.arguments not in (None, ""):
                items = op.arguments if isinstance(op.arguments, tuple) else (op.arguments,)
                if not all(isinstance(item, str) for item in items):
                    raise ManifestError("Invalid dependency arguments")
            if op.environment is not None and not isinstance(op.environment, dict):
                raise ManifestError(f"Bottles dependency {name} has an invalid environment")
        elif isinstance(op, OverrideDll):
            if op.url:
                raise ManifestError(f"Bottles dependency {name} uses a dynamic DLL override")
            overrides: list[tuple[Optional[object], Optional[object]]] = []
            if op.bundle:
                for item in op.bundle:
                    if not isinstance(item, dict):
                        raise ManifestError("Invalid DLL override")
                    overrides.append((item.get("value"), item.get("data")))
            else:
                overrides.append((op.dll, op.type))
            for dll, mode in overrides:
                if not isinstance(dll, str) or not isinstance(mode, str):
                    raise ManifestError("Invalid DLL override")
        else:
            raise ManifestError(f"Bottles dependency {name} uses unsupported UMU action {op.spelling}")
