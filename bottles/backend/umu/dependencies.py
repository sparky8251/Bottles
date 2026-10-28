from collections.abc import Iterable
from gettext import gettext as _
from typing import Callable, Optional

from bottles.backend.models.result import Result
from bottles.backend.steps.executors.umu import UmuDependencyError, UmuStepExecutor
from bottles.backend.steps.grammar import Program, ProgramKind
from bottles.backend.steps.machine import run
from bottles.backend.steps.yaml_frontend import ManifestError, check_umu_target, parse
from bottles.backend.umu.executor import UmuExecutor
from bottles.backend.umu.models import UmuGame
from bottles.backend.umu.repository import UmuGameRepository

__all__ = ["UmuDependencyError", "UmuDependencyInstaller"]


class UmuDependencyInstaller:
    def __init__(
        self,
        manager,
        repository: UmuGameRepository,
        executor: UmuExecutor,
    ):
        self.manager = manager
        self.repository = repository
        self.executor = executor

    def _plan(self, names: Iterable[str], installed: set[str]) -> list[tuple[str, Program]]:
        planned: list[tuple[str, Program]] = []
        visiting: set[str] = set()
        visited = set(installed)

        def visit(name):
            if name in visited:
                return
            if name in visiting:
                raise UmuDependencyError(f"Cyclic dependency chain for {name}")

            manifest = self.manager.dependency_manager.get_dependency(name)
            if not isinstance(manifest, dict):
                raise UmuDependencyError(f"Cannot find Bottles dependency {name}")
            dependencies = manifest.get("Dependencies", [])
            steps = manifest.get("Steps")
            if not isinstance(dependencies, list) or not isinstance(steps, list):
                raise UmuDependencyError(f"Invalid Bottles dependency {name}")
            if not all(isinstance(dependency, str) for dependency in dependencies):
                raise UmuDependencyError(f"Invalid dependency chain for {name}")
            try:
                program = parse(manifest, ProgramKind.DEPENDENCY, name)
            except ManifestError as error:
                raise UmuDependencyError(error.message) from error

            visiting.add(name)
            for dependency in program.header.dependencies:
                visit(dependency)
            try:
                check_umu_target(program)
            except ManifestError as error:
                raise UmuDependencyError(error.message) from error
            visiting.remove(name)
            visited.add(name)
            planned.append((name, program))

        for name in names:
            if not isinstance(name, str) or not name:
                raise UmuDependencyError("Invalid Bottles dependency name")
            visit(name)
        return planned

    def is_compatible(self, name: str, installed: Iterable[str] = ()) -> bool:
        try:
            self._plan((name,), set(installed))
        except (OSError, ValueError, UmuDependencyError):
            return False
        return True

    def install(
        self,
        game: UmuGame,
        names: Iterable[str],
        progress_cb: Optional[Callable[[str], None]] = None,
        progress_progress_cb: Optional[Callable[[Optional[float]], None]] = None,
    ) -> Result:
        updated = game
        try:
            if isinstance(names, (str, bytes)):
                raise UmuDependencyError("Invalid Bottles dependency list")
            installed_values = game.extra.get("installed_dependencies", [])
            if not isinstance(installed_values, list) or not all(
                isinstance(name, str) for name in installed_values
            ):
                raise UmuDependencyError("Invalid installed dependency metadata")
            installed = set(installed_values)
            plan = self._plan(names, installed)
            environment = dict(game.environment)
            completed = list(installed)
            for name, program in plan:
                if progress_cb:
                    progress_cb(_('Installing "{0}"...').format(name))
                executor = UmuStepExecutor(
                    self.manager,
                    updated,
                    self.executor,
                    environment,
                    progress_cb=progress_cb,
                    progress_progress_cb=progress_progress_cb,
                )
                # umu never filtered steps by arch, so every opcode runs
                run(program, executor, None)
                completed.append(name)
                updated = self.repository.update(
                    updated,
                    environment=environment,
                    extra={
                        **updated.extra,
                        "installed_dependencies": sorted(set(completed)),
                    },
                )
            if progress_cb:
                progress_cb(_("Finalizing installation..."))
        except (OSError, ValueError, UmuDependencyError) as error:
            return Result(False, data=updated, message=str(error))
        return Result(True, data=updated)
