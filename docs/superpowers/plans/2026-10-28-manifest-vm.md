# Manifest VM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four manifest step interpreters with one YAML frontend, one typed opcode set, one machine, and two executors, with byte-identical traces over the pinned manifest corpus.

**Architecture:** `bottles/backend/steps/` holds the grammar (frozen dataclasses per opcode), the YAML frontend (dict to `Program`, one validator), the machine (one loop over `program.ops` against an `Executor` Protocol), and two executors whose method bodies are today's handlers moved verbatim. The three managers keep their lifecycle code and call `parse` then `run`. A recording executor plus golden traces captured from the old code before any move is the oracle.

**Tech Stack:** Python 3.13 (flatpak) / 3.14 (host, cpak), PyYAML, dataclasses, typing.Protocol, pytest, mypy, meson install lists.

**Spec:** `docs/superpowers/specs/2026-10-28-manifest-vm-design.md`

## Global Constraints

- Observable behaviour on the pinned corpus is byte-identical; handler bodies move verbatim, no rewrites inside them.
- The frontend performs no optimization: one opcode per step, manifest order, fields as written, never dropped on arch.
- `grammar.py` and `yaml_frontend.py` import only the standard library and PyYAML.
- Only two legacy reservations exist, installer kind only: `set_windows` and `check_file_exist`. Every other action outside its kind is a parse error.
- Error messages that reach the UI keep today's text: `"Invalid manifest for {name}."`, `"Cannot find manifest for {name}."`, `"Cyclic dependency chain for {name}."`, `"Files for {name} are not available in offline mode."`, `"One or more steps failed for {name}."`, `"Bottles dependency {name} uses unsupported UMU action {action}"`, and the other `UmuDependencyError` strings in `umu/dependencies.py`.
- `for` matching is `arch in value` exactly as today: substring on a string, membership on a list. Default `"win64_win32"`.
- No tree-wide reformatting. New files follow ruff's configured rules; nothing else is touched for style.
- Commits carry the repo's AI trailers and the `Co-Authored-By` line; commits happen only when the user says so.

## Review Focus

1. A manifest whose `Steps` is a list containing a non-dict: parse must raise `ManifestError("Invalid manifest for {name}.")`, never `TypeError`. Pinned in Task 3.
2. `for` given as a list with the bottle arch, e.g. `["win64"]` on a win64 bottle: op runs; on win32: skipped. Pinned in Task 4.
3. A step with `url: temp/...` in `is_dependency_cached`: it is skipped for the cache check, as today. Pinned in Task 7.
4. An installer `install_exe` with `url: local` and a missing entry in `local_resources`: today raises `KeyError` inside `__perform_steps`. The moved body keeps that; Task 8's wiring must not catch it (behaviour preserved, noted as a pre-existing bug in the commit message).
5. umu `_plan` on a manifest whose `Steps` has a dict with a non-string `action`: today's message is `"Bottles dependency {name} uses unsupported UMU action 3"`; the one validator now rejects it first with `"Invalid manifest for {name}."`, which `_plan` re-raises as `UmuDependencyError`. The corpus never hits this; the new message is pinned in Task 9 so the change is deliberate and visible.

---

### Task 1: Corpus fixtures and golden traces from the old code

**Files:**
- Create: `bottles/tests/backend/steps/__init__.py` (empty)
- Create: `bottles/tests/backend/steps/corpus/README.md`
- Create: `bottles/tests/backend/steps/corpus/dependencies/**/*.yml` (127 manifests)
- Create: `bottles/tests/backend/steps/corpus/installers/**/*.yml` (40 manifests)
- Create: `bottles/tests/backend/steps/legacy_capture.py`
- Create: `bottles/tests/backend/steps/golden/dependencies.jsonl`, `installers.jsonl`, `umu.jsonl`

**Interfaces:**
- Produces: the trace line format every later trace test compares against:
  `{"name": str, "kind": "dependency"|"installer", "arch": "win64"|"win32"|null, "parse": "ok"|"error: <message>", "uninstaller": bool|null, "ops": [{"op": str, "skipped": bool, "fields": {str: json}}]}`.
  `fields` is the step dict minus `action` and `for`, keys sorted, values as parsed from YAML. `op` is the opcode name from the spec's tables, mapped from the handler the old code invoked. umu lines have `arch: null` and `parse` is the `is_compatible` verdict plus the `_plan` message on rejection.

- [ ] **Step 1: Copy the corpus**

From the pinned checkouts (SHAs in `bottles/backend/managers/repository.py`): every `*.yml` under `dependencies/` except `index.yml`, `testing.yml`, `2022.8.28-brescia-2.yml`, `.github/`; every `*.yml` under `programs/` except `index.yml` and `_trash/`. Keep the directory layout. `README.md` records the three SHAs and the exclusions.

Run: `find bottles/tests/backend/steps/corpus -name '*.yml' | wc -l`
Expected: `167`

- [ ] **Step 2: Write `legacy_capture.py`**

A script, not a test (no `test_` prefix), run with `-I` from the repo root. For each dependency manifest and each arch, it instantiates `DependencyManager` with a stub manager, monkeypatches every `_DependencyManager__step_*` attribute and the inline `install_exe` path's `component_manager.download` with recorders that return success, patches `TaskManager`, `update_config`, `RegistryRuleManager.apply_rules`, and `versioning_manager`, then calls `install(config, [name, {}])` and writes one line. Validation failures are recorded as `parse: "error: <Result.message>"` and no ops. The stub manager has `supported_dependencies = {}` so prerequisites are never recursed, and `_DependencyManager__manifests` is preloaded from the corpus files so `get_dependency` never touches the network. For installers it patches `__step_run_script`, `__step_run_winecommand`, `__step_update_config`, `__component_manager.download`, and `WineExecutor`, sets `_InstallerManager__local_resources` to a `defaultdict` returning a placeholder path (so `url: local` steps record instead of raising), then calls `_InstallerManager__perform_steps` directly with the manifest's `Steps`; for umu it calls `UmuDependencyInstaller.is_compatible` and records `_plan`'s exception message. The handler-to-opcode map lives in the script and matches the spec tables.

- [ ] **Step 3: Capture and inspect**

Run: `.../venv/bin/python -I bottles/tests/backend/steps/legacy_capture.py bottles/tests/backend/steps/corpus bottles/tests/backend/steps/golden`
Expected: three files; `wc -l` gives 254 (127×2), 40, 127. `grep -c legacy_ golden/installers.jsonl` gives 2 (Star Citizen, itch.io). `grep -c '"skipped": true' golden/dependencies.jsonl` is greater than 0.

- [ ] **Step 4: Commit** (on the user's go)

```bash
git add bottles/tests/backend/steps docs/superpowers/specs/2026-10-28-manifest-vm-design.md docs/superpowers/plans/2026-10-28-manifest-vm.md
git commit -m "steps: pin manifest corpus and golden traces from the current interpreters"
```

---

### Task 2: Grammar

**Files:**
- Create: `bottles/backend/steps/__init__.py` (empty)
- Create: `bottles/backend/steps/grammar.py`
- Test: `bottles/tests/backend/steps/test_grammar.py`

**Interfaces:**
- Produces:
  - `class ProgramKind(Enum)`: `DEPENDENCY = "dependency"`, `INSTALLER = "installer"`.
  - `ARCH_BOTH = "win64_win32"`.
  - `@dataclass(frozen=True) class Op`: field `arches: str | tuple[str, ...] = ARCH_BOTH`; ClassVars `opcode: str`, `spellings: tuple[str, ...]`, `kind: ProgramKind`, `keeps_uninstaller: bool`, `halts_on_failure: bool`; methods `applies_to(arch: str) -> bool` (returns `arch in self.arches`), `describe() -> str`, `fields() -> dict[str, object]` (non-None dataclass fields except `arches`, sorted).
  - One subclass per opcode in the spec tables, named in CamelCase (`DownloadArchive`, `InstallFile`, `ArchiveExtract`, `CabExtract`, `GetFromCab`, `CopyFile`, `DeleteDlls`, `InstallFonts`, `RegisterDll`, `OverrideDll`, `SetRegisterKey`, `RegisterFont`, `ReplaceFont`, `SetWindows`, `UseWindows`, `Uninstall`, `InstallerRunFile`, `RunScript`, `RunWinecommand`, `UpdateConfig`, `LegacySetWindows`, `LegacyCheckFileExist`), fields exactly as the spec tables, all `Optional` with default `None` except where the corpus always supplies them (`url`, `file_name` for download-shaped ops; `dlls` for `RegisterDll`/`DeleteDlls`; `script`; `commands`). `commands` is `tuple[dict, ...]`, `bundle` is `tuple[dict, ...] | None`, `fonts`/`dlls`/`replace` are `tuple[str, ...]`.
  - `keeps_uninstaller` is False for `CabExtract`, `GetFromCab`, `ArchiveExtract`, `InstallFonts`, `CopyFile`; True otherwise. `halts_on_failure` is True for `DownloadArchive`, `InstallFile`, `CabExtract`, `GetFromCab`, `ArchiveExtract`, `InstallFonts`, `CopyFile`, `InstallerRunFile`, `RunScript`, `RunWinecommand`; False otherwise (today's if-chain ignores those handlers' return values).
  - `describe()` returns the strings from `__describe_step` (`dependency.py:288-323`) for dependency opcodes; installer opcodes return the `activity` strings their handlers log today (`"installer script"`, the command basename, the file basename), legacy opcodes return `"Skipping {spelling}"`.
  - `OPCODES: dict[ProgramKind, dict[str, type[Op]]]` mapping spelling to class.
  - `@dataclass(frozen=True) class DependencyHeader`: `name: str`, `dependencies: tuple[str, ...]`, `uninstaller: str | None`, `conflicts: tuple[str, ...]`.
  - `@dataclass(frozen=True) class InstallerHeader`: `name: str`, `dependencies: tuple[str, ...]`, `parameters: dict | None`, `executables: tuple[dict, ...]`, `checks: dict | None`, `installers: tuple[str, ...]`.
  - `@dataclass(frozen=True) class Program`: `kind: ProgramKind`, `header: DependencyHeader | InstallerHeader`, `ops: tuple[Op, ...]`; methods `cacheable_downloads(arch: str | None) -> list[tuple[str, str]]` (for ops with a `url` not starting with `temp/`, applying to `arch` when given: `(rename or file_name, file_checksum or "")`), `local_resource_files() -> list[str]` (`file_name` of `InstallerRunFile` ops with `url == "local"`), `dump() -> str` (one line per op: `opcode`, then `for=<arches>` if not default, then `key=value` pairs from `fields()`).

- [ ] **Step 1: Implement `grammar.py`**

No imports beyond `dataclasses`, `enum`, `typing`, `gettext`.

- [ ] **Step 2: Check the table matches the spec**

Run: `python -I -c "from bottles.backend.steps.grammar import OPCODES, ProgramKind as K; print(len(OPCODES[K.DEPENDENCY]), len(OPCODES[K.INSTALLER]))"`
Expected: `19 7` (16 opcodes + 3 synonym spellings; 4 opcodes + 1 synonym spelling + 2 legacy spellings).

- [ ] **Step 3: Add tests**

```python
def test_arches_substring_and_list_semantics():
    assert InstallFile(url="u", file_name="f").applies_to("win32")
    assert InstallFile(url="u", file_name="f", arches=("win64",)).applies_to("win64")
    assert not InstallFile(url="u", file_name="f", arches=("win64",)).applies_to("win32")
    assert InstallFile(url="u", file_name="f", arches="win64").applies_to("win64")

def test_every_opcode_has_a_unique_name_and_kind_specific_spellings():
    names = [cls.opcode for table in OPCODES.values() for cls in set(table.values())]
    assert len(names) == len(set(names)) == 22

def test_dump_is_one_line_per_op_in_order(): ...

def test_cacheable_downloads_skips_temp_urls_and_respects_arch():  # Review Focus 3
    ops = (InstallFile(url="https://x/a.exe", file_name="a.exe", file_checksum="1"),
           CopyFile(url="temp/pkg", file_name="b.dll", dest="win64"),
           InstallFile(url="https://x/c.exe", file_name="c.exe", rename="r.exe", file_checksum="2", arches=("win64",)))
    program = Program(ProgramKind.DEPENDENCY, DependencyHeader("d", (), None, ()), ops)
    assert program.cacheable_downloads("win32") == [("a.exe", "1")]
    assert program.cacheable_downloads("win64") == [("a.exe", "1"), ("r.exe", "2")]
```

Run: `pytest bottles/tests/backend/steps/test_grammar.py -v`
Expected: PASS

- [ ] **Step 4: Commit** (on the user's go)

---

### Task 3: YAML frontend

**Files:**
- Create: `bottles/backend/steps/yaml_frontend.py`
- Test: `bottles/tests/backend/steps/test_yaml_frontend.py`

**Interfaces:**
- Consumes: Task 2 grammar.
- Produces:
  - `class ManifestError(ValueError)`: `.message` is the UI string.
  - `parse(manifest: object, kind: ProgramKind, name: str) -> Program`. Raises `ManifestError("Cannot find manifest for {name}.")` when `manifest` is not a dict; `ManifestError("Invalid manifest for {name}.")` on any shape failure, using exactly the acceptance rules of `DependencyManager.install` (`dependency.py:338-363`) for dependencies, and for installers the rules `InstallerManager.install` applies (`installer.py:688-704`: `Executables`/`Executable` must yield a non-empty list of dicts with `file` and `name`) plus `Steps` being a list of dicts with a string `action`. A spelling not in `OPCODES[kind]` is a shape failure. Field values are taken as-is; lists become tuples.
  - `parse_file(path: str, kind: ProgramKind) -> Program` (`yaml.safe_load`; name from `Name` or the file stem).
  - `check_umu_target(program: Program) -> None`: raises `ManifestError` with the `UmuDependencyError` messages from `umu/dependencies.py:55-63, 90-118` for any op that is not `InstallFile` or `OverrideDll`, `temp/` URLs, `url` on overrides, non-string argument items, non-dict environments.

- [ ] **Step 1: Implement `yaml_frontend.py`**

Imports: `yaml`, `grammar`, standard library only.

- [ ] **Step 2: Parse the corpus**

Run: `for f in bottles/tests/backend/steps/corpus/dependencies/**/*.yml; do python -I -m bottles.backend.steps check dependency "$f" || echo FAIL $f; done` (the module entry exists after Task 11; until then use a two-line `python -I -c` loop calling `parse_file`).
Expected: no FAIL for dependencies; no FAIL for installers; `grep -l 'check_file_exist\|action: set_windows' corpus/installers/**/*.yml` names exactly `itchio.yml` and `starcitizen.yml`, and their dumps contain `legacy_check_file_exist` / `legacy_set_windows`.

- [ ] **Step 3: Add tests**

```python
def test_rejects_non_dict_step_with_the_ui_message():
    with pytest.raises(ManifestError) as e:
        parse({"Steps": ["x"]}, ProgramKind.DEPENDENCY, "dep")
    assert e.value.message == "Invalid manifest for dep."

def test_rejects_installer_action_in_dependency_kind():  # run_script in a dependency
def test_rejects_unknown_installer_action_but_accepts_the_two_legacy_spellings():
def test_synonyms_map_to_one_opcode():  # install_msi -> InstallFile, copy_dll -> CopyFile
def test_for_is_kept_as_written():  # ["win64"] -> ("win64",), absent -> ARCH_BOTH
def test_umu_target_messages_match_the_old_validator():
    # "uses unsupported UMU action set_windows", "uses a temporary source", "uses a dynamic DLL override"
def test_frontend_imports_nothing_from_gtk_or_wine(tmp_path):
    # subprocess: python -I -c "import sys, bottles.backend.steps.yaml_frontend; bad=[m for m in sys.modules if m.startswith(('gi','bottles.backend.wine','bottles.backend.managers'))]; print(bad)"
    # assert output == "[]"
```

Run: `pytest bottles/tests/backend/steps/test_yaml_frontend.py -v`
Expected: PASS

- [ ] **Step 4: Commit** (on the user's go)

---

### Task 4: Machine, executor Protocol, recording executor

**Files:**
- Create: `bottles/backend/steps/machine.py`
- Create: `bottles/tests/backend/steps/recording.py`
- Test: `bottles/tests/backend/steps/test_machine.py`

**Interfaces:**
- Consumes: Task 2 grammar; `bottles.backend.models.result.Result`.
- Produces:
  - `class Executor(Protocol)`: one method per opcode, named by `Op.opcode`, signature `def <opcode>(self, op: <OpClass>) -> Result`. 22 methods.
  - `@dataclass class Callbacks`: `progress: Callable[[str], None] | None = None` (receives `op.describe()` before each op), `fraction: Callable[[float | None], None] | None = None` (receives `None` before each op, as `__notify_progress_fraction` does today), `step: Callable[[], None] | None = None` (called before each op, the installer's `step_fn`).
  - `run(program: Program, executor: Executor, arch: str, callbacks: Callbacks | None = None) -> Result`: the spec loop. Skipped ops do not trigger callbacks (today's `continue` comes before `__notify_progress`; today's installer calls `step_fn` before every step and has no arch filter, which is identical because installer ops always carry `ARCH_BOTH`). Returns the executor's failing `Result` unchanged when a halting op fails; otherwise `Result(True, data={"uninstaller": flag})`.
  - `RecordingExecutor` (tests): records `(op.opcode, op.fields())` per call, returns `Result(True)`; constructor takes `fail_on: set[str] = ()` to return `Result(False, message="boom")` for named opcodes; `trace(program, arch) -> dict` builds the Task 1 line format including skipped ops.

- [ ] **Step 1: Implement `machine.py` and `recording.py`**

- [ ] **Step 2: Add tests**

```python
def test_skips_ops_whose_arches_exclude_the_bottle(): ...  # Review Focus 2
def test_halting_failure_returns_the_executor_result_and_stops(): ...
def test_non_halting_failure_continues(): ...  # OverrideDll returning False
def test_uninstaller_flag_clears_on_extract_ops_only(): ...
def test_callbacks_fire_in_order_and_not_for_skipped_ops(): ...
def test_recording_executor_defines_every_opcode():
    for table in OPCODES.values():
        for cls in set(table.values()):
            assert callable(getattr(RecordingExecutor, cls.opcode))
```

Run: `pytest bottles/tests/backend/steps/test_machine.py -v`
Expected: PASS

- [ ] **Step 3: Commit** (on the user's go)

---

### Task 5: Bottle executor, dependency half

**Files:**
- Create: `bottles/backend/steps/executors/__init__.py` (empty), `bottles/backend/steps/executors/bottle.py`
- Modify: `bottles/backend/managers/dependency.py:600-1093` (remove the moved handlers; keep `install`, `is_dependency_cached`, `fetch_catalog`, `get_dependency`, `__dependency_has_cycle`, `__notify_progress*`, `__build_progress_handler`)
- Modify: `bottles/tests/backend/manager/test_dependency_extraction.py`

**Interfaces:**
- Consumes: Task 2 opcodes, Task 4 `Executor`.
- Produces: `class BottleStepExecutor` with `__init__(self, manager, config: BottleConfig, *, task: Task | None = None, progress_cb=None, progress_fraction_cb=None, step_fn=None, progress_fn=None, activity_fn=None, log_fn=None, local_resources: dict | None = None)` and the 16 dependency methods. Each body is the corresponding `__step_*` body with `step.get("x")` replaced by `op.x` and `self.__manager` by `self.manager`; the two inline paths (`__perform_steps`'s `uninstaller`/`return` handling) are now the machine's job. Methods for non-halting opcodes return `Result(True)` after the body, matching today's ignored return. `get_real_dest(config, dest)` is a module-level function in `bottle.py` (was `__get_real_dest`). The module ends with `_check: Executor` assignment of a `BottleStepExecutor` only under `TYPE_CHECKING`, so mypy checks conformance inside the package.

- [ ] **Step 1: Move the sixteen bodies**

Move, do not rewrite. `__step_install_exe_msi` becomes `install_file`. Imports move with them.

- [ ] **Step 2: Re-point `test_dependency_extraction.py`**

`_DependencyManager__step_get_from_cab(...)` → `BottleStepExecutor(manager, config).get_from_cab(GetFromCab(...))`; `__step_install_fonts` → `.install_fonts(InstallFonts(...))`; the `__get_real_dest` monkeypatch targets `bottles.backend.steps.executors.bottle.get_real_dest`.

Run: `pytest bottles/tests/backend/manager/test_dependency_extraction.py -v`
Expected: PASS, same assertions.

- [ ] **Step 3: Commit** (on the user's go)

---

### Task 6: Bottle executor, installer half

**Files:**
- Modify: `bottles/backend/steps/executors/bottle.py`
- Modify: `bottles/backend/managers/installer.py:203-302, 303-583` (remove `__perform_steps` and the three step handlers plus `__progress_paths`, `__progress_positions`, `__watch_progress`; keep `install`, `__perform_checks`, `__set_parameters`, `__install_dependencies`, `__register_executable`, `__download_icon`, `__process_local_resources`, catalog methods)
- Modify: `bottles/tests/backend/manager/test_installer.py:399-700`

**Interfaces:**
- Produces: `BottleStepExecutor.installer_run_file`, `run_script`, `run_winecommand`, `update_config`, `legacy_set_windows`, `legacy_check_file_exist`. `installer_run_file` is the inline block at `installer.py:248-302` verbatim, reading `self.local_resources[op.file_name]` for `url == "local"` (Review Focus 4: the `KeyError` stays). `run_winecommand` carries `progress_positions`, `watch_progress`, `progress_paths` as module-level functions in `bottle.py`. The two legacy methods call `logging.warning(f"Skipping unsupported installer step {op.opcode}")` and return `Result(True)`.

- [ ] **Step 1: Move the bodies**

- [ ] **Step 2: Re-point `test_installer.py`**

`InstallerManager._InstallerManager__step_run_winecommand(config, step, ...)` → `BottleStepExecutor(manager, config, progress_fn=..., activity_fn=..., log_fn=...).run_winecommand(RunWinecommand(commands=...))`. The one `__perform_steps` call (line 564) → `run(parse({"Name": ..., "Executable": {...}, "Steps": steps}, ProgramKind.INSTALLER, "x"), BottleStepExecutor(...), config.Arch, Callbacks(step=step_fn))`. `__watch_progress`/`__progress_positions` references → the module-level functions.

Run: `pytest bottles/tests/backend/manager/test_installer.py -v`
Expected: PASS, same assertions.

- [ ] **Step 3: Pin Review Focus 4**

```python
def test_local_file_missing_from_local_resources_raises_key_error_as_before():
    executor = BottleStepExecutor(manager, config, local_resources={})
    with pytest.raises(KeyError):
        executor.installer_run_file(InstallerRunFile(url="local", file_name="setup.exe"))
```

Run: `pytest bottles/tests/backend/manager/test_installer.py -k local_resources -v`
Expected: PASS. The commit message records this as pre-existing behaviour kept on purpose.

- [ ] **Step 4: Commit** (on the user's go)

---

### Task 7: Wire `DependencyManager`

**Files:**
- Modify: `bottles/backend/managers/dependency.py` (`install`, `is_dependency_cached`)
- Test: `bottles/tests/backend/steps/test_traces.py` (new), existing `test_dependency_install.py`, `test_dependency_removal.py`, `test_offline_cache.py`

**Interfaces:**
- Consumes: `parse`, `run`, `Callbacks`, `BottleStepExecutor`, `Program.cacheable_downloads`.
- Produces: unchanged public signatures `install(config, dependency, progress_cb, progress_progress_cb) -> Result`, `is_dependency_cached(name, arch, installed, checked, resolved) -> bool`.

- [ ] **Step 1: Replace the validation block and step loop in `install`**

`parse(manifest, ProgramKind.DEPENDENCY, dependency[0])` inside `try`; `ManifestError` → `Result(status=False, message=e.message)`. Prerequisites still come from `program.header.dependencies`. The step loop becomes one `run(...)` call with `Callbacks(progress=lambda m: self.__notify_progress(progress_cb, m, task=task), fraction=lambda v: self.__notify_progress_fraction(progress_progress_cb, v))`; on failure the message is wrapped as today (`"One or more steps failed for {name}." + " " + res.message`). The `uninstaller` logic after the loop reads `res.data["uninstaller"]` and `program.header.uninstaller`.

- [ ] **Step 2: Replace the step walk in `is_dependency_cached`**

Parse failure → `checked.discard(name); return False`. The file loop iterates `program.cacheable_downloads(arch)` and calls `is_cached_file(file, checksum, self.__checksum_cache)` as today (Review Focus 3 covered by `cacheable_downloads` skipping `temp/`).

- [ ] **Step 3: Run the existing suites**

Run: `TMPDIR=/data/projects/bottles/.worktrees/.tmp .../venv/bin/pytest bottles/tests/backend/manager/test_dependency_install.py bottles/tests/backend/manager/test_dependency_removal.py bottles/tests/backend/manager/test_offline_cache.py -v`
Expected: PASS; any test reaching into a removed private name is re-pointed, assertions unchanged.

- [ ] **Step 4: Add the dependency trace test**

```python
@pytest.mark.parametrize("line", load_golden("dependencies.jsonl"))
def test_dependency_trace_matches_the_old_interpreter(line):
    path = corpus_path("dependencies", line["name"])
    try:
        program = parse_file(path, ProgramKind.DEPENDENCY)
    except ManifestError as e:
        assert line["parse"] == f"error: {e.message}"; return
    assert line["parse"] == "ok"
    assert RecordingExecutor().trace(program, line["arch"]) == line
```

Run: `pytest bottles/tests/backend/steps/test_traces.py -v -k dependency`
Expected: 254 PASS.

- [ ] **Step 5: Commit** (on the user's go)

---

### Task 8: Wire `InstallerManager`

**Files:**
- Modify: `bottles/backend/managers/installer.py` (`install`, `count_steps`, `has_local_resources`)
- Test: `bottles/tests/backend/steps/test_traces.py`, existing `test_installer.py`

- [ ] **Step 1: Parse once at the top of `install`**

`parse(manifest, ProgramKind.INSTALLER, installer[0])`; `ManifestError` → the existing `Result(False, data={"message": ...})` shapes (`"Cannot find manifest for {name}."` / `"Installer is not well configured."`). Header fields replace the seven `manifest.get(...)` reads. The `steps` block becomes `run(program, BottleStepExecutor(self.__manager, _config, step_fn=..., progress_fn=..., activity_fn=..., log_fn=..., local_resources=self.__local_resources), config.Arch, Callbacks(step=step_fn if is_final else None))` with the same `is_final` gating and the same failure wrapping.

- [ ] **Step 2: `count_steps` and `has_local_resources` read the program**

Signatures unchanged (the installer window calls them). `count_steps` counts from `program.header` and `len(program.ops)`; `has_local_resources` returns `program.local_resource_files()`.

- [ ] **Step 3: Run the suite and add the installer trace test**

Same shape as Task 7 step 4 over `installers.jsonl` with `arch` from the line (the capture used win64). Also assert the Star Citizen and itch.io lines contain one `legacy_*` op each.

Run: `pytest bottles/tests/backend/manager/test_installer.py bottles/tests/backend/steps/test_traces.py -v`
Expected: PASS; 40 installer trace lines.

- [ ] **Step 4: Commit** (on the user's go)

---

### Task 9: umu executor and wiring

**Files:**
- Create: `bottles/backend/steps/executors/umu.py`
- Modify: `bottles/backend/umu/dependencies.py` (`_plan`, `_validate_step`, `install`; remove `_apply_override`, `_install_executable`, `_arguments`, `SUPPORTED_ACTIONS`)
- Test: `bottles/tests/backend/umu/test_dependencies.py` (unchanged), `test_traces.py`

**Interfaces:**
- Produces: `class UmuStepExecutor` with `__init__(self, manager, game: UmuGame, process_executor, environment: dict, progress_cb=None, progress_progress_cb=None)`, methods `install_file` (body of `_install_executable`, reading `self.environment`) and `override_dll` (body of `_apply_override` onto `self.environment`); the other 20 methods raise `AssertionError("unreachable: rejected by check_umu_target")`. `_arguments` becomes a module-level function.

- [ ] **Step 1: Rewrite `_plan` on top of `parse` + `check_umu_target`**

Cycle detection, visited/installed bookkeeping, and the `(name, program)` list stay; `ManifestError` → `UmuDependencyError(e.message)`. `install` runs each program with `run(program, UmuStepExecutor(...), arch="win64", Callbacks(progress=progress_cb))`, where the executor's `install_file` first calls `progress_cb(_("Downloading {0}...")...)` as today and `override_dll` calls `progress_cb(_("Updating DLL overrides..."))`. The per-dependency `repository.update(...)` after each program stays in `install`. A failing op raises `UmuDependencyError` inside the executor as today, so `install`'s `except` wrapping is unchanged.

- [ ] **Step 2: Run the umu suite unchanged**

Run: `pytest bottles/tests/backend/umu/test_dependencies.py -v`
Expected: 6 PASS, including the exact message `"unsupported UMU action set_windows"`.

- [ ] **Step 3: Add the umu verdict trace test and Review Focus 5**

Over `umu.jsonl`: `check_umu_target` accept/reject and message must match each line. Plus:

```python
def test_umu_plan_reports_malformed_manifest_with_the_parser_message(monkeypatch):
    # get_dependency returns {"Name": "bad", "Dependencies": [], "Steps": [{"action": 3}]}
    assert installer.is_compatible("bad") is False
    with pytest.raises(UmuDependencyError, match=r"Invalid manifest for bad\."):
        installer._plan(("bad",), set())
```

Run: `pytest bottles/tests/backend/steps/test_traces.py -v`
Expected: PASS, 127 umu lines.

- [ ] **Step 4: Commit** (on the user's go)

---

### Task 10: Executor conformance and isolation

**Files:**
- Modify: `mypy.ini`
- Test: `bottles/tests/backend/steps/test_executors.py`

- [ ] **Step 1: Re-enable the codes for the package**

Append to `mypy.ini`:

```ini
# The steps package relies on Protocol conformance; the global disables above
# would hide a missing executor method.
[mypy-bottles.backend.steps.*]
enable_error_code = arg-type, attr-defined, misc, union-attr
```

Run: `.../venv/bin/mypy bottles/backend/steps` (same invocation the pre-commit hook uses)
Expected: `Success`.

- [ ] **Step 2: Add the runtime conformance test**

```python
@pytest.mark.parametrize("executor", [BottleStepExecutor, UmuStepExecutor, RecordingExecutor])
def test_executor_defines_every_opcode(executor):
    missing = [cls.opcode for t in OPCODES.values() for cls in set(t.values()) if not callable(getattr(executor, cls.opcode, None))]
    assert missing == []
```

Run: `pytest bottles/tests/backend/steps/test_executors.py -v`
Expected: PASS.

- [ ] **Step 3: Commit** (on the user's go)

---

### Task 11: CLI, module entry point, meson

**Files:**
- Create: `bottles/backend/steps/__main__.py`
- Modify: `bottles/frontend/cli/cli.py` (subparser after `umu`, `__process_args` branch, new `manage_manifest`)
- Create: `bottles/backend/steps/meson.build`, `bottles/backend/steps/executors/meson.build`
- Modify: `bottles/backend/meson.build` (add `subdir('steps')`)
- Test: `bottles/tests/backend/steps/test_cli.py`

**Interfaces:**
- Produces: `bottles.backend.steps.__main__.main(argv: list[str]) -> int` with `argparse`: `verb {check,dump}`, `kind {dependency,installer}`, `path`. `check` prints nothing on success, the `ManifestError.message` to stderr and returns 1 on failure; `dump` prints `program.dump()`. `bottles-cli manifest VERB KIND SOURCE`: if `SOURCE` is an existing file, delegates to `main`; otherwise builds a `Manager(g_settings=self.settings, is_cli=True)` and resolves the name through `dependency_manager.get_dependency` / `installer_manager.get_installer`, then parses and prints the same way.

- [ ] **Step 1: Implement `__main__.py`, the CLI branch, and the meson lists**

- [ ] **Step 2: Verify from a bare interpreter**

Run: `python -I -m bottles.backend.steps dump installer bottles/tests/backend/steps/corpus/installers/Games/starcitizen.yml | head -3`
Expected: first line `legacy_set_windows version=win10`.
Run: `python -I -m bottles.backend.steps check dependency /dev/null; echo $?`
Expected: `Cannot find manifest for null.` on stderr, `1`.

- [ ] **Step 3: Add the CLI test**

```python
def test_check_and_dump_run_without_gtk(tmp_path):
    # subprocess [sys.executable, "-I", "-m", "bottles.backend.steps", "dump", "dependency", corpus("Essentials/vcredist2010.yml")]
    # assert returncode == 0 and "install_file" in stdout and stdout.count("\n") == 6
```

Run: `pytest bottles/tests/backend/steps/test_cli.py -v`
Expected: PASS.

- [ ] **Step 4: Hooks and the full host suite**

Run: `pre-commit run --all-files` then `TMPDIR=/data/projects/bottles/.worktrees/.tmp .../venv/bin/pytest bottles/tests --ignore=bottles/tests/backend/integration/playtime -q`
Expected: hooks 7/7; failures limited to the three pre-existing ones (sandbox_creation, frontend, installer_dialog collection).

- [ ] **Step 5: Commit** (on the user's go)

---

### Task 12: Artifacts and live check

**Files:**
- Modify: scratchpad `step10/live-check.py` → copy to `scratchpad/step12/live-check.py` with one more check.

- [ ] **Step 1: Build both artifacts**

Run: `build-aux/build.sh` (Devel manifest) and the cpak podman build.
Expected: both succeed; the cpak final stage's strict checker passes; `flatpak run --command=bottles-check-deps com.usebottles.bottles.Devel --strict` passes.

- [ ] **Step 2: Module entry inside both artifacts**

Tests are not installed, so point the entry at a corpus file on the host.

Run: `flatpak run --filesystem=/data/projects/bottles --command=python3 com.usebottles.bottles.Devel -I -m bottles.backend.steps dump dependency /data/projects/bottles/.worktrees/deps-67.5/bottles/tests/backend/steps/corpus/dependencies/Essentials/vcredist2010.yml`
Expected: six lines, exit 0. Same command inside the cpak container with the repo bind-mounted, same output.

- [ ] **Step 3: Live check with a real dependency install**

Add to `live-check.py`: create a throwaway bottle, run `manager.dependency_manager.install(config, ["vcredist2010", {}])`, assert `result.ok` and `"vcredist2010" in config.Installed_Dependencies`, then delete the bottle. The user runs it because it needs a runner.
Expected: ok, plus the existing seven checks ok.

- [ ] **Step 4: Report numbers**

Flatpak size, cpak size, host suite pass count, hook status, trace test counts (254 + 40 + 127), lines removed from the three managers.
