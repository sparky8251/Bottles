# Manifest corpus

Copies of the dependency and installer manifests Bottles 67.5 fetches at
runtime, at the commits pinned in `bottles/backend/managers/repository.py`:

- dependencies: bottlesdevs/dependencies @ 23bd12fd717c506addb0b05859a5f8635036f225
  (all `*.yml` except `index.yml`, `testing.yml`, `2022.8.28-brescia-2.yml`, `.github/`)
- installers: bottlesdevs/programs @ 9a21e3997f19e8388d2100c3500369ee2fe0239c
  (all `*.yml` except `index.yml` and `_trash/`)

The golden traces in `../golden/` were produced from these files against
the interpreters as they were before the steps package existed (commit
31aabeb6): `*.jsonl` by `../legacy_capture.py` (what enters the executor:
opcode, fields, arch skip, uninstaller flag) and `egress-*.jsonl` by
`../legacy_egress_capture.py` (what leaves it: every side-effect call with
its arguments). Regenerate either only from that commit; the egress
script's header has the worktree incantation.

What the golden traces cannot see: values are JSON-normalised, so Python
type (list vs tuple) is not part of the contract (a unit test pins it), and
`__describe_step` was replaced by a recorder during capture, so progress
text is checked by unit tests rather than by the traces.
