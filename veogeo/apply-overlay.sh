#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
overlay_root="$repo_root/veogeo"
patch_file="$overlay_root/patches/geonode-cloud-overlay.patch"
materialized_patch="$(mktemp "${TMPDIR:-/tmp}/geonoxt-overlay.XXXXXX")"
trap 'rm -f "$materialized_patch"' EXIT
python3 - "$patch_file" "$materialized_patch" <<'MATPATCH'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_bytes()
markers = {
    b"<VEOGEO_BLANK_CONTEXT>": b" ",
    b"<VEOGEO_SPACE>": b" ",
    b"<VEOGEO_TAB>": b"\t",
    b"<VEOGEO_CR>": b"\r",
}
if not any(marker in source for marker in markers):
    raise SystemExit("overlay patch whitespace markers are missing")
for marker, value in markers.items():
    source = source.replace(marker, value)
Path(sys.argv[2]).write_bytes(source)
MATPATCH

if git -C "$repo_root" apply --reverse --check "$materialized_patch" >/dev/null 2>&1; then
    echo "GeoNoXT GeoNode integration patch already applied."
else
    # Full blob IDs validate that the patch still targets this upstream base.
    git -C "$repo_root" apply --index --check "$materialized_patch"
    git -C "$repo_root" apply "$materialized_patch"
    python3 - "$repo_root" <<'EOLS'
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
changed = subprocess.check_output(
    ["git", "-C", str(root), "diff", "--name-only"], text=True
).splitlines()
for relative in changed:
    try:
        original = subprocess.check_output(
            ["git", "-C", str(root), "show", f"HEAD:{relative}"],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        continue
    if b"\r\n" in original:
        path = root / relative
        if path.is_file():
            current = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(current.replace(b"\n", b"\r\n"))
EOLS
    echo "Applied GeoNoXT GeoNode integration patch."
fi

mkdir -p "$repo_root/geonoxt"
cp -R "$overlay_root/src/geonoxt/." "$repo_root/geonoxt/"
cp -R "$overlay_root/src/docker/." "$repo_root/docker/"
install -m 755 "$overlay_root/src/seed-cloud-demo.sh" "$repo_root/seed-cloud-demo.sh"
echo "Installed GeoNoXT package and demo utilities."
