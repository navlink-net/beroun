#!/usr/bin/env bash
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
# Babylon.js for the web layer: fetches the pinned npm packages (BABYLON_VERSION, config/versions.env) and writes the UMD builds the
# system serves at /lib/babylon/ into web-shell/lib/babylon/, with SHA256SUMS and the licence. Run only to change the version; the files are in the repository.
#   scripts/babylon-fetch.sh           fetch and write
#   scripts/babylon-fetch.sh --check   verify the files in the repository against SHA256SUMS (no network)
set -euo pipefail
SIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../config/versions.env
. "$SIM_DIR/config/versions.env"
DEST="$SIM_DIR/web-shell/lib/babylon"
# package -> files of it that the system serves (the .min builds, never .d.ts or maps)
FILES=(
  "babylonjs:babylon.js"
  "babylonjs-loaders:babylonjs.loaders.min.js"
  "babylonjs-materials:babylonjs.materials.min.js"
  "babylonjs-gui:babylon.gui.min.js"
)
if [ "${1:-}" = "--check" ]; then
  cd "$DEST" && sha256sum -c SHA256SUMS && [ "$(cat VERSION)" = "$BABYLON_VERSION" ] || { echo "babylon: VERSION is not BABYLON_VERSION=$BABYLON_VERSION" >&2; exit 1; }
  exit 0
fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$DEST"
for pf in "${FILES[@]}"; do
  pkg="${pf%%:*}"; file="${pf#*:}"
  ( cd "$TMP" && npm pack "$pkg@$BABYLON_VERSION" --silent >/dev/null && mkdir -p "x-$pkg" && tar xzf "$pkg-$BABYLON_VERSION.tgz" -C "x-$pkg" )   # npm checks the registry's integrity hash
  cp "$TMP/x-$pkg/package/$file" "$DEST/$file"
  [ "$pkg" = "babylonjs" ] && cp "$TMP/x-$pkg/package/license.md" "$DEST/LICENSE.md"
done
echo "$BABYLON_VERSION" >"$DEST/VERSION"
( cd "$DEST" && sha256sum babylon.js babylonjs.loaders.min.js babylonjs.materials.min.js babylon.gui.min.js LICENSE.md >SHA256SUMS )
echo "babylon $BABYLON_VERSION -> $DEST"; ls -l "$DEST"
