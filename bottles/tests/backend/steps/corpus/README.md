# Manifest corpus

Copies of the dependency and installer manifests Bottles 67.5 fetches at
runtime, at the commits pinned in `bottles/backend/managers/repository.py`:

- dependencies: bottlesdevs/dependencies @ 23bd12fd717c506addb0b05859a5f8635036f225
  (all `*.yml` except `index.yml`, `testing.yml`, `2022.8.28-brescia-2.yml`, `.github/`)
- installers: bottlesdevs/programs @ 9a21e3997f19e8388d2100c3500369ee2fe0239c
  (all `*.yml` except `index.yml` and `_trash/`)

The golden traces in `../golden/` were produced from these files by
`../legacy_capture.py` running against the interpreters as they were
before the steps package existed. Regenerate them only from that commit.
