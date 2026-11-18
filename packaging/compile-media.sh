#!/bin/bash
set -euo pipefail

work=$(realpath "$1")
prefix=$(realpath -m "$2")
cd "$work"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
export LD_LIBRARY_PATH="$prefix/lib"
export GI_TYPELIB_PATH="$prefix/lib/girepository-1.0"
meson=(python3 "$work/meson-1.7.2/meson.py")

build() {
    local src=$1 dest="compile-$1"
    shift
    "${meson[@]}" setup --prefix="$prefix" --libdir=lib --buildtype=release \
        --strip --wrap-mode=nodownload "$@" "$dest" "$src"
    ninja -j "${BUILD_JOBS:-4}" -C "$dest"
    "${meson[@]}" install -C "$dest" --no-rebuild
}

build gstreamer-1.28.9 -Dauto_features=disabled -Dintrospection=enabled -Dtools=enabled
build gst-plugins-base-1.28.9 -Dauto_features=disabled -Dintrospection=enabled -Dapp=enabled
build pipewire-1.6.11 -Dauto_features=disabled -Dgstreamer=enabled -Ddbus=enabled \
    -Dsupport=enabled -Daudioconvert=enabled -Dvideoconvert=enabled \
    -Dlegacy-rtkit=false -Dtests=disabled -Dexamples=disabled -Dsession-managers=
