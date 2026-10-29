# Manifest programs: one grammar, one machine

Date: 2026-10-28. Branch: deps/67.5. Status: design, awaiting review.

## Goal

Dependency and installer manifests are programs. Today four interpreters
run them: `managers/dependency.py` (18 actions), `managers/installer.py`
(6 actions, its own `install_exe`), `umu/dependencies.py` (2 actions), and
`managers/component.py` (`Post: rename`). Three validators check their
shape, each with different rules, and `__describe_step` reads the
vocabulary a fifth time.

This step makes the grammar explicit: a YAML frontend emits a flat list of
typed opcodes, one machine runs the list against an executor, and the
managers keep only bottle bookkeeping. Observable behaviour on the pinned
manifest corpus stays byte-identical. The YAML format stays as it is.

## Non-goals

- New syntax, ranges, `only` blocks, or new opcodes.
- Vendoring the catalogs into the repo.
- Merging the two `install_exe` behaviours.
- Changing where manifests are fetched from.
- The components catalog (`File`/`Post`). It is a table with one post
  action and stays as it is.

## Grammar

Two program kinds share a header shape and no opcodes. Every opcode is a
frozen dataclass whose fields are today's step keys. A program is a header
plus a tuple of opcodes.

### Dependency opcodes

| Opcode | Accepted spellings | Fields |
|---|---|---|
| `download_archive` | `download_archive` | url, file_name, rename, file_checksum, file_size |
| `install_file` | `install_exe`, `install_msi` | url, file_name, rename, file_checksum, file_size, arguments, environment, success_codes |
| `archive_extract` | `archive_extract` | url, file_name, rename, file_checksum, file_size, dest |
| `cab_extract` | `cab_extract` | url, file_name, rename, file_checksum, file_size, dest |
| `get_from_cab` | `get_from_cab` | source, file_name, rename, dest |
| `copy_file` | `copy_dll`, `copy_file` | url, file_name, dest |
| `delete_dlls` | `delete_dlls` | dest, dlls |
| `install_fonts` | `install_fonts`, `install_cab_fonts` | url, fonts |
| `register_dll` | `register_dll` | dlls |
| `override_dll` | `override_dll` | dll, type, bundle, url |
| `set_register_key` | `set_register_key` | key, value, data, type |
| `register_font` | `register_font` | name, file |
| `replace_font` | `replace_font` | font, replace |
| `set_windows` | `set_windows` | version |
| `use_windows` | `use_windows` | version, executable |
| `uninstall` | `uninstall` | file_name |

Spellings collapse only where today's handler is shared and nothing
branches on the name. `install_exe`/`install_msi` is decided by file
extension inside `WineExecutor`. `copy_dll`/`copy_file` and
`install_fonts`/`install_cab_fonts` share one handler each.
`set_windows`/`use_windows` call different functions and stay distinct.

### Installer opcodes

| Opcode | Accepted spellings | Fields |
|---|---|---|
| `installer_run_file` | `install_exe`, `install_msi` | url, file_name, rename, file_checksum, arguments, environment, monitoring |
| `run_script` | `run_script` | script |
| `run_winecommand` | `run_winecommand` | commands; each: command, arguments, minimal, wait, capture_exit_code, success_codes, progress, label, skip_if_files_exist |
| `update_config` | `update_config` | path, type, del_keys, upd_keys |
| `legacy_set_windows` | `set_windows` | version |
| `legacy_check_file_exist` | `check_file_exist` | path |

`installer_run_file` is a different opcode from the dependency
`install_file` because it is a different behaviour: `WineExecutor` with
`monitoring`, a `local` URL convention for user-supplied files, no
success-code list, no staging into the prefix.

The two `legacy_*` opcodes name the two cases that ship today: Star
Citizen's `set_windows` and itch.io's `check_file_exist`, which the
installer interpreter has always run as silent successes. They stay no-ops
that log at warning. In lax mode (see "Two modes") any other action no
opcode defines lowers to the generic `legacy_noop`, carrying the spelling,
because that is what both old loops did; in strict mode all three are
parse errors.

### Two namespaces

Manifest keys and opcode attributes are different namespaces, mapped
explicitly. The keys an opcode accepts are exactly the fields it declares
itself (`manifest_fields`, computed from the dataclass), never anything
inherited from `Op`, so adding an attribute to `Op` can never become a
reserved word in a manifest. A YAML list becomes a tuple only where the
declared type is a tuple, which marks the fields handlers iterate;
everything else keeps the manifest's own type because handlers forward it
as a value (gfw.yml's `arguments: [/nodotnet /q]` is one). A missing key
is `None`, so a moved body fails on it the way the old handler failed on
`step.get(...)`.

### Attributes on every opcode

- `arches`: from `for`, default both. Matched by substring against the
  bottle arch, as today, so a string or a list both work.
- `keeps_uninstaller`: a static class property. Today it is the scattered
  `uninstaller = False` in the if-chain: false for `cab_extract`,
  `get_from_cab`, `archive_extract`, `install_fonts`, `copy_file`; true
  otherwise.
- `describe()`: the progress string, moved from `__describe_step`.

### Headers

- Dependency: name, dependencies, uninstaller, conflicts.
- Installer: name, dependencies, parameters, executables, checks,
  installers. Catalog-only fields the widgets read (`Description`,
  `Category`, `Grade`, `Arch`, `Channel`) stay in the index dicts.

### umu is a target, not a program kind

umu accepts dependency programs whose opcodes are only `install_file` and
`override_dll`. Its extra constraints (no `temp/` URL, no `url` on
overrides, string-typed arguments and environment) are a target check
applied after parsing. Rejections keep today's messages.

### No optimization

The frontend emits one opcode per step, in manifest order, with the
fields as written. It never drops a step whose `arches` exclude a bottle,
never merges, reorders, or deduplicates, and never resolves a field
early. Every decision that depends on the bottle or the filesystem is
made by the machine or the executor at execution time. `dump` therefore
shows the whole program regardless of arch, and a trace shows which
opcodes the machine skipped.

## Machine

The VM is one loop. Input: a program, an executor, a bottle arch, and the
progress callbacks. For each opcode: skip if `arches` excludes the arch;
report `describe()`; call the executor method for that opcode; on failure
stop and return it; clear the uninstaller flag if `keeps_uninstaller` is
false. Output: a result with success and the uninstaller flag. The same
loop runs both program kinds. There are no jumps, no variables, and no
state between opcodes beyond what the executor keeps on disk.

## Executor

A Protocol with one method per opcode, 23 in all (the 22 spelled opcodes plus `legacy_noop`). An executor that omits a
method is a mypy error. Two implementations ship:

- `BottleExecutor`: today's `__step_*` bodies from `dependency.py` and the
  step bodies from `installer.py`, moved verbatim. It holds both sets of
  progress callbacks.
- `UmuExecutor`: `install_file` and `override_dll` from
  `umu/dependencies.py`. The parser's target check guarantees nothing else
  reaches it.

A third, `RecordingExecutor`, lives under `bottles/tests/` and appends each
call with its fields to a trace.

Globs stay operand semantics: `copy_file` and `override_dll` resolve their
pattern against the filesystem when they run, as today. The VM never
expands or inserts opcodes.

## What stays in the managers

The lifecycle around the steps is bottle bookkeeping, not instruction
semantics, and stays where it is, byte for byte: prerequisite resolution
and recursive install, the versioning snapshot, `Installed_Dependencies`
and `Uninstallers` updates, the task-manager entry, the registry-rule
trigger on first install, and the installer's checks, parameter
application, executable registration, and dependent installers. These
loops call parse and run instead of walking dicts.

## YAML frontend

Input: the manifest dict as `get_dependency`/`get_installer` return it,
plus a program kind. Output: a program, or an error carrying the message
today's code produces, since those reach the UI ("Invalid manifest for
X.", "Cannot find manifest for X.").

### Two modes

The YAML frontend is the legacy frontend. It has to stay for several
releases alongside the next grammar so a migration can happen, so by
default it is a drop-in replacement for the old loops, including what they
tolerated: `parse(..., strict=False)` drops a key no opcode defines (the
handlers never read it) and lowers an action no opcode defines to
`legacy_noop` (the if-chains fell through and counted the step a success).
The managers always call the default. With `strict=True` both are errors
and so are the two named legacy spellings; that is the rule the next
grammar's frontend will apply, and what `manifest check --strict` lints a
manifest against today. Nothing else differs between the modes.

One validator replaces three. It takes the strictest accept set, which is
`install`'s, because that is the one that gates execution. Where
`is_dependency_cached` would have accepted something `install` rejects,
the user sees the same rejection one click earlier. Nothing in the pinned
corpus is affected.

The cached-file scan in `is_dependency_cached` becomes a program query.
`count_steps` and `has_local_resources` keep reading the manifest dict,
because the installer dialog sizes itself and asks for local files before
`install()` can refuse the manifest, and the old code did the same.

## CLI

Two verbs, reachable two ways. `dump KIND SOURCE` prints the program one
opcode per line, fields included, in the same form the recording executor
writes. `check KIND SOURCE` parses only and exits non-zero with the
parser's message.

`python -m bottles.backend.steps VERB KIND PATH [--strict]` is the
dependency-free entry point: it takes a YAML path and imports only the
grammar and the frontend. `bottles-cli manifest VERB KIND SOURCE` calls the same function
and also accepts a catalog name, resolved through the Manager;
`bottles-cli` imports GTK at module level, so it carries that
requirement. Both ship in every build.

`mypy.ini` disables `arg-type` and `attr-defined` globally. The grammar,
frontend and machine modules re-enable them, and `steps/conformance.py`, a
type-check-only module under the same settings, assigns each executor to
the `Executor` Protocol so a missing or mis-typed opcode method is a mypy
error. The executors themselves hold handler bodies moved as they were and
keep the global settings. A test asserts at runtime that both executors
define a method for every opcode.

## Layout

```
bottles/backend/steps/
  grammar.py        opcode classes, headers, Program, ProgramKind
  yaml_frontend.py  parse(manifest, kind) -> Program | error
  machine.py        run(program, executor, arch, callbacks) -> Result
  executors/
    bottle.py
    umu.py
bottles/tests/backend/steps/
  recording.py      RecordingExecutor
  test_grammar.py, test_yaml_frontend.py, test_machine.py, test_traces.py
```

`managers/dependency.py`, `managers/installer.py`, and
`umu/dependencies.py` import from the package and keep their lifecycle
code.

`grammar.py` and `yaml_frontend.py` import only the standard library and
PyYAML, so `bottles-cli manifest check` and `dump` run on a host with no
GTK, no Wine, and no bottle. Only the executors import the Wine and
manager modules, and only `machine.py` and the managers import the
executors. A test imports the two modules in a subprocess with `-I` and
asserts no `gi` or `bottles.backend.wine` module loaded.

## Proving identical output

The oracle is a trace diff over the pinned corpus: 127 dependency and 40
installer manifests at the SHAs in `managers/repository.py`, read from a
local checkout.

1. Before the change, drive today's three interpreters with the recorder
   standing in for every side-effecting call, for both bottle arches and
   for umu's accept/reject verdict. Save the traces.
2. After the change, parse and run the same corpus with the recorder. The
   traces must match byte for byte. The Star Citizen and itch.io traces
   must contain the legacy no-ops.
3. Handler bodies move verbatim. The diff of the move is the check on
   behaviour inside each opcode.

Existing behaviour tests keep passing unchanged: `test_dependency_install`,
`test_dependency_extraction`, `test_dependency_removal`, `test_installer`,
`test_offline_cache`, `umu/test_dependencies`. Tests that reach into
private names are re-pointed at the public seam.

Artifacts: Devel flatpak and cpak build, strict checker, and the live
check extended with one real dependency install into a throwaway bottle,
run by the user since it needs a runner.

## Compatibility notes

Byte-identical holds for the pinned corpus, and lax mode extends it to the
inputs the corpus does not cover: unknown keys and actions behave as the
old loops behaved, umu's planner keeps its own shape messages ("Invalid
Bottles dependency X", "Invalid dependency chain for X") ahead of the
parser's, and neither the installer loop nor umu filters steps by `for`
(the machine runs them with `arch=None`; only dependency programs are
arch-filtered, as before).

The deliberate differences, all on malformed manifests outside the corpus:

- `is_dependency_cached` uses the install validator's accept set rather
  than its own slightly looser one, so a manifest the old cache check
  called cached and the install then rejected now shows as not cached.
  Same outcome, one click earlier. It also reads `url` only from opcodes
  that define one; the old walk read `url` from every step.
- umu's planner recurses prerequisites first as before, but parses the
  parent before its own per-step checks, so a step with a non-string `url`,
  `file_name`, `rename`, `file_checksum` or `for`, or a non-dict step,
  reports "Invalid manifest for X." where it used to report "has an
  invalid download" or "unsupported UMU action unknown".
- Three malformed inputs that crashed the old code with a traceback now
  stop with the manifest's error message at the same point, before any
  side effect: an installer `install_exe`/`install_msi` without a `url`
  (old: `KeyError('url')`), an installer whose `Dependencies` is not a
  list (old: iterated whatever it was), and a dependency `uninstall`
  without a `file_name` (old: `KeyError('file_name')`).

List values from the manifest keep their Python type unless the opcode
iterates the field (`dlls`, `fonts`, `replace`, `success_codes`, `bundle`,
`commands`, `monitoring`, `for`), because handlers forward the others as
values into command lines; the recorder's JSON trace cannot see that
distinction, so a unit test pins it.

## Later, and not precluded

- Ranges expand in the frontend to N opcodes, like shell brace expansion.
- A new syntax is a second frontend emitting the same opcodes.
- Vendoring the catalogs makes the parser strict and the two legacy
  reservations removable once the manifests are fixed.
- Merging the two run-file behaviours is a separate behavioural step that
  the trace oracle will measure.
