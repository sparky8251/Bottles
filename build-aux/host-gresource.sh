#!/bin/sh
# Build what the frontend tests need from an installed Bottles, outside meson:
# the UI bundle, linked where the tests expect it (/app/share/bottles, the
# flatpak's pkgdatadir; CI symlinks the installed bundle there too), and the
# GSettings schema, compiled into the build dir for GSETTINGS_SCHEMA_DIR.
# Needs blueprint-compiler, glib-compile-resources and glib-compile-schemas,
# and a writable /app/share/bottles.
#
#   build-aux/host-gresource.sh [build-dir]
#   export GSETTINGS_SCHEMA_DIR=<build-dir>/schemas
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
build=${1:-"$repo/.worktrees/.tmp/host-gresource"}
ui="$repo/bottles/frontend/ui"
target=/app/share/bottles/bottles.gresource
app_id=com.usebottles.bottles

mkdir -p "$build/ui" "$build/schemas"
blueprint-compiler batch-compile "$build/ui" "$ui" "$ui"/*.blp
cp "$ui"/*.css "$build/ui/"
glib-compile-resources --sourcedir "$build/ui" --target "$build/bottles.gresource" "$ui/bottles.gresource.xml"
ln -sfn "$build/bottles.gresource" "$target"

# the tests set params.APP_ID to the release id before importing windows
sed "s/@APP_ID@/$app_id/g" "$repo/data/$app_id.gschema.xml" > "$build/schemas/$app_id.gschema.xml"
glib-compile-schemas "$build/schemas"

echo "linked $target -> $build/bottles.gresource"
echo "export GSETTINGS_SCHEMA_DIR=$build/schemas"
