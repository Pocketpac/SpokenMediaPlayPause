# Building the AppImage

Build in an x86-64 Ubuntu 22.04 environment to target glibc 2.35. The Dockerfile installs the build dependencies. Run these commands from the repository root.

```sh
docker build -t spoken-media-pause-build -f packaging/Dockerfile .
mkdir -p dist
docker run --rm --platform linux/amd64 \
  -v "$PWD:/src:ro" -v "$PWD/dist:/out" \
  spoken-media-pause-build bash /src/packaging/build-appimage.sh /out
```

On an x86-64 Jammy system with the Dockerfile's dependencies installed, run `bash packaging/build-appimage.sh` to write the AppImage to `dist/`. Set `BUILD_JOBS` to choose the number of parallel build jobs. Set `SPOKEN_SOURCE_CACHE` to a directory with previously downloaded dependency archives. The build checks their SHA-256 hashes before use.

The build uses Jammy's Python, GTK, and system packages and builds GStreamer and PipeWire from the pinned upstream sources in [sources.tsv](sources.tsv). It packages the needed libraries and audits the glibc symbol requirements of every bundled ELF file. The output uses the host glibc. System package updates can change the bytes of a rebuilt AppImage.

## Release files

Put the All Players AppImage, the Windows x64 EXE, and all companion addon ZIPs in the same GitHub release. Mark it as Latest and label the Windows download as Beta. List direct download links so users can choose a desktop helper and the ZIP for their WoW client.

Set [the shared release version](../desktop/release-version.txt) before building both companions. Use a matching GitHub release tag such as `v0.5.3` or `addon-v0.5.3`. The update check uses the combined release version, independent of the WoW addon and Windows beta version numbers. Keep the canonical asset names so both companions can identify their downloads.

Stage the two desktop builds, the project license, and the Windows runtime notices in a clean `dist/release` directory. See the [Windows build instructions](../windows/README.md#build-from-source) for the EXE. The addon packager reads the version from the current WoW TOCs and generates both legacy manifests from the same source.

```sh
python3 packaging/package-addon.py dist/release
lua5.1 tests/addon/marker.lua
(
  cd dist/release
  sha256sum -- *.AppImage *.exe *.zip DOTNET-NOTICES.txt LICENSE > SHA256SUMS-all.txt
  sha256sum --check SHA256SUMS-all.txt
)
```

## Bundled dependencies

The source package names and versions for the published Ubuntu binaries are listed in [ubuntu-22.04-components.tsv](ubuntu-22.04-components.tsv). Each source package is available in [Launchpad's Ubuntu source archive](https://launchpad.net/ubuntu/+source) at its listed version. For example, the [GTK 4 source package](https://launchpad.net/ubuntu/+source/gtk4/4.6.9%2Bds-0ubuntu0.22.04.2) corresponds to the published GTK binaries. The AppImage includes package notices and a build manifest under `usr/share/licenses`.

The release's static launcher comes from [AppImage type2-runtime commit fedfad5](https://github.com/AppImage/type2-runtime/tree/fedfad5). If the upstream continuous release changes, use the pinned launcher from a published AppImage with `SPOKEN_SOURCE_CACHE` and verify its digest against [sources.tsv](sources.tsv).

```sh
mkdir -p source-cache
image=Spoken-Media-Pause-All-Players-Ubuntu-22.04-x86_64.AppImage
head -c "$(./"$image" --appimage-offset)" -- "$image" > source-cache/runtime-x86_64
```
