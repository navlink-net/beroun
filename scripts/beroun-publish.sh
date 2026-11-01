#!/usr/bin/env bash
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
# Publishes the packages scripts/beroun-pack.py wrote to the store, as the platform:
#
#   ARBITER=https://navlink.net ADMIN_TOKEN=<X-Admin-Token> scripts/beroun-publish.sh DIR [id ...]
#
# Each DIR/<id>-<version>.zip goes to POST $ARBITER/api/admin/beroun/publish with its listing (DIR/<id>-<version>.json) and
# its icon from the zip; the arbiter signs and approves it at once. The token is read from the environment only, never
# from an argument (it would show in the process list). A version that is already published is refused by the arbiter
# and reported here; the others still go.
set -u
DIR=${1:?usage: ARBITER=... ADMIN_TOKEN=... $0 DIR [id ...]}
shift
: "${ARBITER:?set ARBITER, e.g. https://navlink.net}" "${ADMIN_TOKEN:?set ADMIN_TOKEN}"
PY=$(command -v python3 || command -v python)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
for meta in "$DIR"/*.json; do
    id=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["id"])' "$meta")
    if [ $# -gt 0 ] && ! printf '%s\n' "$@" | grep -qx "$id"; then continue; fi
    zip=${meta%.json}.zip
    "$PY" - "$meta" "$zip" "$TMP" > "$TMP/fields" <<'EOF'
import json, os, sys, zipfile
m = json.load(open(sys.argv[1], encoding="utf-8"))
icon = os.path.join(sys.argv[3], "icon" + os.path.splitext(m["icon"])[1])
with zipfile.ZipFile(sys.argv[2]) as z, open(icon, "wb") as f:
    f.write(z.read(m["icon"]))
for k in ("name", "description", "category"):
    print("--form-string")
    print(k + "=" + m[k])
print("-F")
print("icon=@" + icon)
EOF
    args=()
    while IFS= read -r line; do args+=("$line"); done < "$TMP/fields"
    printf 'X-Admin-Token: %s\n' "$ADMIN_TOKEN" > "$TMP/header"
    out=$(curl -sS -H @"$TMP/header" "${args[@]}" -F "download_beroun_file=@$zip" "$ARBITER/api/admin/beroun/publish")
    rm -f "$TMP/header"
    echo "$id: $out"
    case $out in *'"ok":true'*) ;; *) fail=1 ;; esac
done
exit $fail
