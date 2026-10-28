#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# Pass a manifest path to build something other than the development one,
# e.g. the release manifest com.usebottles.bottles.json.
MANIFEST="${1:-$SCRIPT_DIR/com.usebottles.bottles.Devel.json}"
BUILD_DIR="$PROJECT_DIR/build-dir"
APP_ID="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$MANIFEST")"

echo "==> Building $APP_ID"
echo "    Manifest: $MANIFEST"
echo "    Build dir: $BUILD_DIR"

host-spawn flatpak run org.flatpak.Builder \
    --force-clean \
    --disable-rofiles-fuse \
    --user \
    --install \
    --state-dir "$PROJECT_DIR/.flatpak-builder" \
    "$BUILD_DIR" \
    "$MANIFEST"

echo "==> Build complete. Run with:"
echo "    host-spawn flatpak run $APP_ID"
