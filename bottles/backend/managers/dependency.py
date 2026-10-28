# dependency.py
#
# Copyright 2025 mirkobrombin <brombin94@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, in version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from typing import Callable, Optional

from gettext import gettext as _


from bottles.backend.globals import Paths
from bottles.backend.logger import Logger
from bottles.backend.managers.component import is_cached_file
from bottles.backend.managers.registry_rule import RegistryRuleManager
from bottles.backend.models.config import BottleConfig
from bottles.backend.models.enum import Arch
from bottles.backend.models.result import Result
from bottles.backend.state import Task, TaskManager
from bottles.backend.steps.executors.bottle import BottleStepExecutor
from bottles.backend.steps.grammar import ProgramKind
from bottles.backend.steps.machine import Callbacks, run
from bottles.backend.steps.yaml_frontend import ManifestError, parse
from bottles.backend.utils.manager import ManagerUtils

logging = Logger()



class DependencyManager:
    def __init__(self, manager, offline: bool = False):
        self.__manager = manager
        self.__repo = manager.repository_manager.get_repo(
            "dependencies",
            offline,
            callback_in_main_loop=not manager.is_cli,
        )
        self.__offline = offline
        self.__checksum_cache = {}
        self.__manifests = {}

    def get_dependency(self, name: str, plain: bool = False) -> str | dict | bool:
        key = (name, plain)
        if key not in self.__manifests:
            manifest = self.__repo.get(name, plain)
            if manifest:
                self.__manifests[key] = manifest
            return manifest
        return self.__manifests[key]

    def fetch_catalog(self) -> dict:
        """
        Fetch all dependencies from the Bottles repository
        and return these as a dictionary. It also returns an empty dictionary
        if there are no dependencies or fails to fetch them.
        """
        catalog = {}
        index = self.__repo.catalog
        if not isinstance(index, dict):
            return catalog

        for dependency in index.items():
            if not isinstance(dependency[0], str) or not dependency[0]:
                continue
            if not isinstance(dependency[1], dict):
                continue
            category = dependency[1].get("Category")
            description = dependency[1].get("Description")
            arch = dependency[1].get("Arch", "win64_win32")
            if (
                not isinstance(category, str)
                or not category
                or not isinstance(description, str)
                or not (
                    isinstance(arch, str)
                    or isinstance(arch, list)
                    and all(isinstance(item, str) for item in arch)
                )
            ):
                continue
            catalog[dependency[0]] = dependency[1]

        catalog = dict(sorted(catalog.items()))
        if getattr(self, "_DependencyManager__offline", False):
            for name, dependency in catalog.items():
                dependency["Cached"] = {
                    Arch.WIN32: self.is_dependency_cached(name, Arch.WIN32),
                    Arch.WIN64: self.is_dependency_cached(name, Arch.WIN64),
                }
        return catalog

    def is_dependency_cached(
        self,
        name: str,
        arch: Optional[str] = None,
        installed: Optional[list] = None,
        checked: Optional[set] = None,
        resolved: Optional[set] = None,
    ) -> bool:
        if installed is None:
            installed = []
        if checked is None:
            checked = set()
        if resolved is None:
            resolved = set()
        if name in installed or name in resolved:
            return True
        if name in checked:
            return False
        checked.add(name)

        try:
            program = parse(self.get_dependency(name), ProgramKind.DEPENDENCY, name)
        except ManifestError:
            checked.discard(name)
            return False

        for dependency in program.header.dependencies:
            if not self.is_dependency_cached(
                dependency,
                arch=arch,
                installed=installed,
                checked=checked,
                resolved=resolved,
            ):
                checked.discard(name)
                return False

        for file, checksum in program.cacheable_downloads(arch):
            if not file or not is_cached_file(
                file,
                checksum,
                getattr(self, "_DependencyManager__checksum_cache", None),
            ):
                checked.discard(name)
                return False

        checked.discard(name)
        resolved.add(name)
        return True

    def __dependency_has_cycle(
        self,
        name: str,
        installed: Optional[list] = None,
        visiting: Optional[set] = None,
        resolved: Optional[set] = None,
    ) -> bool:
        if installed is None:
            installed = []
        if name in installed:
            return False
        if visiting is None:
            visiting = set()
        if resolved is None:
            resolved = set()
        if name in visiting:
            return True
        if name in resolved:
            return False

        manifest = self.get_dependency(name)
        if not isinstance(manifest, dict):
            return False
        dependencies = manifest.get("Dependencies", [])
        if not isinstance(dependencies, list):
            return False

        visiting.add(name)
        for dependency in dependencies:
            if isinstance(dependency, str) and self.__dependency_has_cycle(
                dependency, installed, visiting, resolved
            ):
                visiting.discard(name)
                return True
        visiting.discard(name)
        resolved.add(name)
        return False

    @staticmethod
    def __notify_progress(
        callback: Optional[Callable[[str], None]],
        message: str,
        task: Optional[Task] = None,
    ) -> None:
        if not message:
            return

        if task is not None:
            task.subtitle = message

        if callback:
            callback(message)

    @staticmethod
    def __notify_progress_fraction(
        callback: Optional[Callable[[Optional[float]], None]], fraction: Optional[float]
    ) -> None:
        if callback:
            callback(fraction)

    def install(
        self,
        config: BottleConfig,
        dependency: list,
        progress_cb: Optional[Callable[[str], None]] = None,
        progress_progress_cb: Optional[Callable[[Optional[float]], None]] = None,
    ) -> Result:
        """
        Install a given dependency in a bottle. It will
        return True if the installation was successful.
        """
        uninstaller = True
        installed_new = False
        try:
            program = parse(
                self.get_dependency(dependency[0]), ProgramKind.DEPENDENCY, dependency[0]
            )
        except ManifestError as error:
            return Result(status=False, message=error.message)
        dependencies = program.header.dependencies
        if self.__dependency_has_cycle(
            dependency[0], installed=config.Installed_Dependencies
        ):
            return Result(
                status=False,
                message=f"Cyclic dependency chain for {dependency[0]}.",
            )

        if getattr(
            self, "_DependencyManager__offline", False
        ) and not self.is_dependency_cached(
            dependency[0],
            arch=config.Arch,
            installed=config.Installed_Dependencies,
        ):
            return Result(
                status=False,
                message=f"Files for {dependency[0]} are not available in offline mode.",
            )

        if config.Parameters.versioning_automatic:
            """
            If the bottle has the versioning system enabled, we need
            to create a new version of the bottle, before installing
            the dependency.
            """
            self.__manager.versioning_manager.create_state(
                config=config, message=f"Before installing {dependency[0]}"
            )

        task_id = TaskManager.add(Task(title=dependency[0]))
        task = TaskManager.get(task_id)

        self.__notify_progress(progress_cb, _("Preparing installation..."), task=task)

        logging.info(
            "Installing dependency [%s] in bottle [%s]." % (dependency[0], config.Name),
        )

        if dependencies:
            """
            If the manifest has dependencies, we need to install them
            before installing the current one.
            """
            for _ext_dep in dependencies:
                if _ext_dep in config.Installed_Dependencies:
                    continue
                if _ext_dep in self.__manager.supported_dependencies:
                    _dep = self.__manager.supported_dependencies[_ext_dep]
                    self.__notify_progress(
                        progress_cb,
                        _("Installing prerequisite “{0}”…").format(_ext_dep),
                        task=task,
                    )
                    _res = self.install(
                        config,
                        [_ext_dep, _dep],
                        progress_cb=progress_cb,
                        progress_progress_cb=progress_progress_cb,
                    )
                    if not _res.status:
                        TaskManager.remove(task_id)
                        return _res

        # Here we execute all steps in the manifest.
        # Steps are the actions performed to install the dependency.
        executor = BottleStepExecutor(
            self.__manager,
            config,
            task=task,
            progress_cb=progress_cb,
            progress_fraction_cb=progress_progress_cb,
        )
        callbacks = Callbacks(
            progress=lambda message: self.__notify_progress(progress_cb, message, task=task),
            fraction=lambda fraction: self.__notify_progress_fraction(
                progress_progress_cb, fraction
            ),
        )
        res = run(program, executor, config.Arch, callbacks)
        if not res.ok:
            TaskManager.remove(task_id)
            message = f"One or more steps failed for {dependency[0]}."
            if res.message:
                message += f" {res.message}"
            return Result(status=False, message=message)
        if not res.data.get("uninstaller"):
            uninstaller = False

        if dependency[0] not in config.Installed_Dependencies:
            """
            If the dependency is not already listed in the installed
            dependencies list of the bottle, add it.
            """
            dependencies = [dependency[0]]

            if config.Installed_Dependencies:
                dependencies = config.Installed_Dependencies + [dependency[0]]

            self.__manager.update_config(
                config=config, key="Installed_Dependencies", value=dependencies
            )
            installed_new = True

        if program.header.uninstaller:
            """
            If the manifest has an uninstaller, add it to the
            uninstaller list in the bottle config.
            Set it to NO_UNINSTALLER if the dependency cannot be uninstalled.
            """
            uninstaller = program.header.uninstaller

        if not isinstance(uninstaller, str) or not uninstaller:
            uninstaller = "NO_UNINSTALLER"
        self.__manager.update_config(
            config, dependency[0], uninstaller, "Uninstallers"
        )

        # Remove entry from task manager
        TaskManager.remove(task_id)

        # Hide installation button and show remove button
        logging.info(f"Dependency installed: {dependency[0]} in {config.Name}", jn=True)

        if installed_new:
            RegistryRuleManager.apply_rules(config, trigger="dependencies")
        self.__notify_progress_fraction(progress_progress_cb, None)
        self.__notify_progress(progress_cb, _("Finalizing installation..."), task=task)
        return Result(
            status=True,
            data={"uninstaller": uninstaller != "NO_UNINSTALLER"},
        )
