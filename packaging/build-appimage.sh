#!/bin/bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ "$(uname -m)" != x86_64 || "$(getconf GNU_LIBC_VERSION)" != 'glibc 2.35' ]]; then
    printf '%s\n' 'Build on x86-64 Ubuntu 22.04. See packaging/README.md.' >&2
    exit 1
fi
destination=${1:-"$repo/dist"}
mkdir -p -- "$destination"
destination=$(realpath "$destination")
artifact=Spoken-Media-Pause-All-Players-Ubuntu-22.04-x86_64.AppImage
if [[ -e "$destination/$artifact" ]]; then
    printf 'File already exists at %s\n' "$destination/$artifact" >&2
    exit 1
fi
work=$(mktemp -d --tmpdir="$destination" .spoken-build.XXXXXX)
trap 'rm -r -- "$work"' EXIT

while IFS=$'\t' read -r name digest url; do
    cached=${SPOKEN_SOURCE_CACHE:-}
    if [[ -n $cached && -f "$cached/$name" ]]; then
        cp -- "$cached/$name" "$work/$name"
    else
        curl --fail --silent --show-error --location --retry 3 \
            --proto '=https' --output "$work/$name" "$url"
    fi
    printf '%s  %s\n' "$digest" "$work/$name" | sha256sum --check
    case "$name" in
        *.tar.gz|*.tar.xz) tar --no-same-owner -xf "$work/$name" -C "$work" ;;
    esac
done < "$repo/packaging/sources.tsv"

prefix="$work/runtime-libs"
bash "$repo/packaging/compile-media.sh" "$work" "$prefix"
LD_LIBRARY_PATH="$prefix/lib" python3 "$repo/packaging/stage_appdir.py" \
    "$repo" "$work" "$prefix" "$work/AppDir"
python3 "$repo/packaging/audit_elf.py" "$work/AppDir" "$work/runtime-x86_64"
mksquashfs "$work/AppDir" "$work/payload.squashfs" \
    -noappend -all-root -no-progress -comp zstd -Xcompression-level 18 -processors "${BUILD_JOBS:-4}"
cat "$work/runtime-x86_64" "$work/payload.squashfs" > "$work/$artifact"
chmod 755 "$work/$artifact"
ln -- "$work/$artifact" "$destination/$artifact"
sha256sum -- "$destination/$artifact"
