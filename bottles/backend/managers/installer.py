# installer_manager.py
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
import os
import uuid
from functools import lru_cache
from typing import Callable, Optional

import markdown

from bottles.backend.utils.httpclient import ICON_TIMEOUT, HttpError, fetch
from bottles.backend.logger import Logger
from bottles.backend.models.config import BottleConfig
from bottles.backend.models.result import Result
from bottles.backend.steps.executors.bottle import BottleStepExecutor
from bottles.backend.steps.grammar import ProgramKind
from bottles.backend.steps.machine import Callbacks, run
from bottles.backend.steps.yaml_frontend import ManifestError, parse
from bottles.backend.utils.manager import ManagerUtils
from bottles.backend.utils.wine import WineUtils
from bottles.backend.wine.regkeys import RegKeys

logging = Logger()


class InstallerManager:
    def __init__(self, manager, offline: bool = False):
        self.__manager = manager
        self.__repo = manager.repository_manager.get_repo(
            "installers",
            offline,
            callback_in_main_loop=not manager.is_cli,
        )
        self.__component_manager = manager.component_manager
        self.__local_resources = {}
        self.__manifests = {}

    @lru_cache
    def get_review(self, installer_name, parse: bool = True) -> str:
        """Return an installer review from the repository (as HTML)"""
        review = self.__repo.get_review(installer_name)
        if not review:
            return "No review found for this installer."
        if parse:
            return markdown.markdown(review)
        return review

    def get_installer(
        self, installer_name: str, plain: bool = False
    ) -> str | dict | bool:
        """
        Return an installer manifest from the repository. Use the plain
        argument to get the manifest as plain text.
        """
        key = (installer_name, plain)
        if key not in self.__manifests:
            manifest = self.__repo.get(installer_name, plain)
            if manifest:
                self.__manifests[key] = manifest
            return manifest
        return self.__manifests[key]

    def fetch_catalog(self) -> dict:
        """Fetch the installers catalog from the repository"""
        catalog = {}
        index = self.__repo.catalog
        if not isinstance(index, dict):
            return catalog

        for installer in index.items():
            catalog[installer[0]] = installer[1]

        catalog = dict(sorted(catalog.items()))
        return catalog

    def refresh_catalog(self) -> dict:
        self.__repo.refresh()
        self.__manifests.clear()
        self.get_review.cache_clear()
        return self.fetch_catalog()

    def get_icon_url(self, installer):
        """Wrapper for the repo method."""
        return self.__repo.get_icon(installer)

    @staticmethod
    def supports_channel(installer, include_unstable=False):
        channel = installer[1].get("Channel", "stable")
        return include_unstable or channel not in ("rc", "unstable")

    @staticmethod
    def supports_runner(installer, runner):
        runners = installer[1].get("Runners")
        if not runners:
            return True
        if not isinstance(runners, list):
            return False
        return any(
            isinstance(candidate, str)
            and (runner == candidate or runner.startswith(f"{candidate}-"))
            for candidate in runners
        )

    def __download_icon(self, config, executable: dict, manifest):
        """
        Download the installer icon from the repository to the bottle
        icons path.
        """
        icon_url = self.__repo.get_icon(manifest.get("Name"))
        bottle_icons_path = f"{ManagerUtils.get_bottle_path(config)}/icons"
        icon_path = f"{bottle_icons_path}/{executable.get('icon')}"

        if icon_url is not None:
            if not os.path.exists(bottle_icons_path):
                os.makedirs(bottle_icons_path)

            if not os.path.isfile(icon_path):
                try:
                    icon = fetch(icon_url, timeout=ICON_TIMEOUT)
                    with open(icon_path, "wb") as f:
                        f.write(icon)
                except (HttpError, OSError) as e:
                    logging.error(f"Failed to download icon '{icon_url}': {e}")
                    if os.path.isfile(icon_path):
                        os.remove(icon_path)

    def __process_local_resources(self, exe_msi_steps, installer):
        files = self.has_local_resources(installer)
        if not files:
            return True
        for file in files:
            if file not in exe_msi_steps.keys():
                return False
            self.__local_resources[file] = exe_msi_steps[file]
        return True

    def __install_dependencies(
        self,
        config: BottleConfig,
        dependencies: list,
        step_fn: callable,
        is_final: bool = False,
    ):
        """Install a list of dependencies"""
        _config = config

        for dep in dependencies:
            if is_final:
                step_fn(dep)

            if dep in config.Installed_Dependencies:
                continue

            _dep = [dep, self.__manager.supported_dependencies.get(dep)]
            res = self.__manager.dependency_manager.install(_config, _dep)

            if not res.ok:
                detail = res.message or (res.data or {}).get("message")
                message = f"Failed to install {dep}."
                if detail:
                    message += f" {detail}"
                return Result(False, message=message)

        return Result(True)

    @staticmethod
    def __perform_checks(config, checks: dict):
        """Perform a list of checks"""
        bottle_path = ManagerUtils.get_bottle_path(config)

        if files := checks.get("files"):
            for f in files:
                if f.startswith("userdir/"):
                    current_user = os.getenv("USER")
                    f = f.replace("userdir/", f"users/{current_user}/")

                _f = os.path.join(bottle_path, "drive_c", f)
                if not os.path.exists(_f):
                    logging.error(
                        f"During checks, file {_f} was not found, assuming it is not installed. Aborting."
                    )
                    return False

        return True

    def __set_parameters(self, config: BottleConfig, new_params: dict):
        _config = config

        if "dxvk" in new_params and isinstance(new_params["dxvk"], bool):
            if new_params["dxvk"] != config.Parameters.dxvk:
                self.__manager.install_dll_component(
                    _config, "dxvk", remove=not new_params["dxvk"]
                )

        if "vkd3d" in new_params and isinstance(new_params["vkd3d"], bool):
            if new_params["vkd3d"] != config.Parameters.vkd3d:
                self.__manager.install_dll_component(
                    _config, "vkd3d", remove=not new_params["vkd3d"]
                )

        if "dxvk_nvapi" in new_params and isinstance(new_params["dxvk_nvapi"], bool):
            if new_params["dxvk_nvapi"] != config.Parameters.dxvk_nvapi:
                self.__manager.install_dll_component(
                    _config, "nvapi", remove=not new_params["dxvk_nvapi"]
                )

        if "latencyflex" in new_params and isinstance(new_params["latencyflex"], bool):
            if new_params["latencyflex"] != config.Parameters.latencyflex:
                self.__manager.install_dll_component(
                    _config, "latencyflex", remove=not new_params["latencyflex"]
                )

        if "decorated" in new_params and isinstance(new_params["decorated"], bool):
            RegKeys(config).set_decorated(new_params["decorated"])

        # avoid sync type change if not set to "wine"
        if "sync" in new_params and config.Parameters.sync != "wine":
            del new_params["sync"]

        for k, v in new_params.items():
            self.__manager.update_config(
                config=config, key=k, value=v, scope="Parameters"
            )

    def __program(self, name: str):
        try:
            return parse(self.get_installer(name), ProgramKind.INSTALLER, name)
        except ManifestError:
            return None

    def count_steps(self, installer) -> dict:
        program = self.__program(installer[0])
        steps = {"total": 0, "sections": []}
        if program is None:
            return steps
        header = program.header
        if header.dependencies:
            i = len(header.dependencies)
            steps["sections"] += i * ["deps"]
            steps["total"] += i
        if header.parameters:
            steps["sections"].append("params")
            steps["total"] += 1
        if program.ops:
            i = len(program.ops)
            steps["sections"] += i * ["steps"]
            steps["total"] += i
        if header.executables:
            steps["sections"].append("exe")
            steps["total"] += 1
        if header.checks:
            steps["sections"].append("checks")
            steps["total"] += 1
        return steps

    def has_local_resources(self, installer):
        program = self.__program(installer[0])
        if program is None:
            return []
        return program.local_resource_files()

    def install(
        self,
        config: BottleConfig,
        installer: dict,
        step_fn: callable,
        is_final: bool = True,
        local_resources: Optional[dict] = None,
        progress_fn: Optional[Callable[[Optional[float]], None]] = None,
        activity_fn: Optional[Callable[[Optional[str]], None]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        manifest = self.get_installer(installer[0])
        try:
            program = parse(manifest, ProgramKind.INSTALLER, installer[0])
        except ManifestError as error:
            if error.message.startswith("Cannot find manifest"):
                return Result(False, data={"message": error.message})
            logging.error("Installer manifest has no valid executable block.")
            return Result(False, data={"message": error.message})
        _config = config
        header = program.header

        installers = header.installers
        dependencies = header.dependencies
        parameters = header.parameters
        executables = header.executables
        checks = header.checks
        skip_missing = header.optional_executables

        for item in executables:
            if item.get("icon"):
                self.__download_icon(_config, item, manifest)

        # install dependent installers
        if installers:
            logging.info("Installing dependent installers")
            for i in installers:
                result = self.install(config, i, step_fn, False)
                if not result.ok:
                    logging.error("Failed to install dependent installer(s)")
                    return Result(
                        False,
                        data={"message": "Failed to install dependent installer(s)"},
                    )

        # ask for local resources
        if local_resources:
            if not self.__process_local_resources(local_resources, installer):
                return Result(
                    False, data={"message": "Local resources not found or invalid"}
                )

        # install dependencies
        if dependencies:
            logging.info("Installing dependencies")
            result = self.__install_dependencies(
                _config, dependencies, step_fn, is_final
            )
            if not result.ok:
                if log_fn:
                    log_fn(result.message)
                return Result(
                    False, data={"message": result.message}, message=result.message
                )

        # set parameters
        if parameters:
            logging.info("Updating bottle parameters")
            if is_final:
                step_fn()

            self.__set_parameters(_config, parameters)

        # execute steps
        if program.ops:
            logging.info("Executing installer steps")
            executor = BottleStepExecutor(
                self.__manager,
                _config,
                progress_fn=progress_fn if is_final else None,
                activity_fn=activity_fn if is_final else None,
                log_fn=log_fn if is_final else None,
                local_resources=self.__local_resources,
            )
            result = run(
                program,
                executor,
                _config.Arch,
                Callbacks(step=step_fn if is_final else None),
            )
            if not result.ok:
                message = result.message or "Installer step failed."
                return Result(
                    False,
                    data={"message": message},
                    message=message,
                )

        # execute checks
        if checks:
            logging.info("Executing installer checks")
            if is_final:
                step_fn()
                if not self.__perform_checks(_config, checks):
                    return Result(
                        False,
                        data={
                            "message": "Checks failed, the program is not installed."
                        },
                    )

        registered = 0
        for item in executables:
            if self.__register_executable(_config, item, skip_missing):
                registered += 1

        if not registered:
            logging.error("No installer executable was found.")
            return Result(
                False,
                data={"message": "Checks failed, the program is not installed."},
            )

        if is_final:
            step_fn()

        logging.info(
            f"Program installed: {header.name} in {config.Name}.", jn=True
        )
        return Result(True)

    def __register_executable(self, config, executable, skip_missing=False):
        bottle = ManagerUtils.get_bottle_path(config)
        exec_path = executable.get("path", "")
        if exec_path.startswith("userdir/"):
            _userdir = WineUtils.get_user_dir(bottle)
            exec_path = exec_path.replace(
                "userdir/", f"/users/{_userdir}/"
            )

        if skip_missing:
            unix_path = os.path.join(bottle, "drive_c", exec_path.lstrip("/"))
            if not os.path.isfile(unix_path):
                return False

        _path = f"C:\\{exec_path}".replace("/", "\\")
        _uuid = str(uuid.uuid4())
        _program = {
            "executable": executable["file"],
            "arguments": executable.get("arguments", ""),
            "name": executable["name"],
            "path": _path,
            "id": _uuid,
        }

        optional_fields = (
            "d7vk",
            "dxvk",
            "vkd3d",
            "dxvk_nvapi",
            "gamescope",
            "virtual_desktop",
            "winebridge",
            "hide_console",
            "sync",
            "environment",
            "folder",
            "pre_script",
            "post_script",
            "pre_script_args",
            "post_script_args",
        )
        for field in optional_fields:
            if field in executable:
                _program[field] = executable[field]

        duplicates = [
            k for k, v in config.External_Programs.items() if v["path"] == _path
        ]
        ext = config.External_Programs

        if duplicates:
            for d in duplicates:
                file_extensions = ext[d].get("file_extensions")
                if file_extensions:
                    _program["file_extensions"] = file_extensions
                    break
            for d in duplicates:
                del ext[d]
            ext[_uuid] = _program
            self.__manager.update_config(
                config=config, key="External_Programs", value=ext
            )
        else:
            self.__manager.update_config(
                config=config, key=_uuid, value=_program, scope="External_Programs"
            )

        # create Desktop entry
        bottles_icons_path = os.path.join(ManagerUtils.get_bottle_path(config), "icons")
        icon = executable.get("icon")
        icon_path = os.path.join(bottles_icons_path, icon) if icon else ""
        ManagerUtils.create_desktop_entry(config, _program, False, icon_path)

        return True
