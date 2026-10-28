#!/usr/bin/env python3
# Derives the development flatpak manifest from the release one so the two
# can only differ in the ways written here: the app id and the meson profile.
# Any other difference between the development and shipped sandboxes would be
# drift, which is what having two hand-edited manifests produced before.
#
# Run from anywhere; a pre-commit hook runs it and fails if the output changed.
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RELEASE = os.path.join(HERE, "com.usebottles.bottles.json")
DEVEL = os.path.join(HERE, "com.usebottles.bottles.Devel.json")


def main():
    with open(RELEASE, encoding="utf-8") as f:
        manifest = json.load(f)

    manifest["id"] += ".Devel"

    for module in manifest["modules"]:
        if isinstance(module, dict) and module["name"] == "bottles":
            opts = [o for o in module.get("config-opts", []) if not o.startswith("-Dprofile=")]
            module["config-opts"] = ["-Dprofile=development", *opts]
            break
    else:
        sys.exit("release manifest has no 'bottles' module")

    out = json.dumps(manifest, indent=4, ensure_ascii=False) + "\n"
    try:
        with open(DEVEL, encoding="utf-8") as f:
            unchanged = f.read() == out
    except FileNotFoundError:
        unchanged = False
    if not unchanged:
        with open(DEVEL, "w", encoding="utf-8") as f:
            f.write(out)
    print(("unchanged: " if unchanged else "wrote: ") + os.path.relpath(DEVEL))


if __name__ == "__main__":
    main()
