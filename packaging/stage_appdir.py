import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

repo, work, prefix, out = map(Path, sys.argv[1:])
out.mkdir()
lib = out / "usr/lib"
multiarch = Path("/usr/lib/x86_64-linux-gnu")
media = prefix / "lib"
packages = set()
owners = {}
for package in Path("/var/lib/dpkg/info").glob("*.list"):
    for path in package.read_text().splitlines():
        if path.startswith("/lib/"):
            path = "/usr" + path
        owners[path] = package.stem

elfs = []
def copy(source, relative):
    source = Path(source)
    dest = out / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    for path in (source, source.resolve()):
        key = str(path)
        if key.startswith("/lib/"):
            key = "/usr" + key
        if key in owners:
            packages.add(owners[key])
    with dest.open("rb") as file:
        if file.read(4) == b"\x7fELF":
            elfs.append(source)

copy("/usr/bin/python3.10", "usr/bin/python3.10")
for subdir in ("python3.10", "python3/dist-packages"):
    root = Path("/usr/lib") / subdir
    for path in sorted(root.rglob("*")):
        if subdir.endswith("dist-packages") and path.relative_to(root).parts[0] not in (
                "gi", "Xlib", "six.py", "PyGObject-3.42.1.egg-info",
                "python_xlib-0.29.egg-info", "six-1.16.0.egg-info"):
            continue
        relative = path.relative_to(root)
        if path.is_file() and not any(part in ("__pycache__", "test", "tests", "config-3.10-x86_64-linux-gnu")
                                       for part in relative.parts):
            copy(path, "usr/lib" / Path(subdir) / relative)

for filename in """
GLib-2.0 GModule-2.0 GObject-2.0 Gio-2.0 Gdk-4.0 GdkWayland-4.0 GdkX11-4.0 GdkPixbuf-2.0
Graphene-1.0 Gsk-4.0 Gtk-4.0 HarfBuzz-0.0 Pango-1.0 PangoCairo-1.0 PangoFT2-1.0
Gst-1.0 GstApp-1.0 GstVideo-1.0 GstBase-1.0 cairo-1.0 freetype2-2.0 fontconfig-2.0 xlib-2.0
""".split():
    path = "girepository-1.0/" + filename + ".typelib"
    copy((media if filename.startswith("Gst") else multiarch) / path, "usr/lib/" + path)

for soname in """
libgtk-4.so.1 libgstapp-1.0.so.0 libgstvideo-1.0.so.0 libfreetype.so.6
""".split():
    copy((media if soname.startswith("libgst") else multiarch) / soname, lib / soname)

for filename in ("libgstapp.so", "libgstcoreelements.so", "libgstpipewire.so"):
    path = "gstreamer-1.0/" + filename
    copy(media / path, "usr/lib/" + path)
scanner = prefix / "libexec/gstreamer-1.0/gst-plugin-scanner"
copy(scanner, "usr/lib/gstreamer-1.0/gst-plugin-scanner")

for filename in ("rt", "protocol-native", "client-node", "client-device", "adapter", "metadata", "session-manager"):
    path = f"pipewire-0.3/libpipewire-module-{filename}.so"
    copy(media / path, "usr/lib/" + path)
for subdir in ("support", "audioconvert", "videoconvert"):
    for source in sorted((media / "spa-0.2" / subdir).glob("*.so")):
        copy(source, lib / "spa-0.2" / subdir / source.name)
copy(prefix / "share/pipewire/client.conf", "usr/share/pipewire/client.conf")

host_glibc = set("libc.so.6 libm.so.6 libmvec.so.1 libdl.so.2 libpthread.so.0 librt.so.1 "
                 "libresolv.so.2 libutil.so.1 libanl.so.1 ld-linux-x86-64.so.2".split())
copied = {p.name for p in lib.glob("*.so*")}
for source in list(elfs):
    result = subprocess.check_output(["ldd", source], text=True)
    if "not found" in result:
        raise RuntimeError(f"Missing build dependency for {source}: {result}")
    for soname, dep in re.findall(r"^\s+(\S+) => (/\S+) ", result, flags=re.M):
        if soname not in copied | host_glibc:
            copy(dep, lib / soname)
            copied.add(soname)

(lib / "gio/modules").mkdir(parents=True)
for path in sorted((repo / "desktop").glob("*.py")):
    copy(path, "usr/share/spoken-media-pause/" + path.name)
for filename in ("spoken-media-pause.desktop", "spoken-media-pause.svg"):
    copy(repo / "packaging" / filename, filename)
os.symlink("spoken-media-pause.svg", out / ".DirIcon")
copy(repo / "packaging/AppRun", "AppRun")
copy(repo / "desktop/README.txt", "README.txt")

# Keep the bundled Fontconfig library paired with its configuration version.
# The configuration still discovers system and user fonts at their usual paths.
copy("/etc/fonts/fonts.conf", "usr/share/spoken-media-pause/fontconfig/fonts.conf")
for config in sorted(Path("/etc/fonts/conf.d").glob("*.conf")):
    copy(config, f"usr/share/spoken-media-pause/fontconfig/conf.d/{config.name}")

licenses = out / "usr/share/licenses"
licenses.mkdir(parents=True)
(licenses / "components.txt").write_text(subprocess.check_output([
    "dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n", *sorted(packages),
], text=True))
for package in sorted(packages):
    name = package.split(":")[0]
    path = Path("/usr/share/doc") / name / "copyright"
    if path.exists():
        copy(path, licenses / f"{name}.txt")
copy(repo / "packaging/type2-runtime.LICENSE", licenses / "AppImage-type2-runtime.txt")
copy(work / "gstreamer-1.28.9/COPYING", licenses / "GStreamer.txt")
copy(work / "gst-plugins-base-1.28.9/COPYING", licenses / "GStreamer-plugins-base.txt")
copy(work / "pipewire-1.6.11/COPYING", licenses / "PipeWire.txt")
copy(repo / "packaging/compile-media.sh", licenses / "build-media-components.sh")
with (licenses / "components.txt").open("a") as manifest:
    manifest.write("""\nRuntime components built on Ubuntu 22.04
GStreamer 1.28.9 https://gstreamer.freedesktop.org/src/gstreamer/gstreamer-1.28.9.tar.xz
GStreamer Base 1.28.9 https://gstreamer.freedesktop.org/src/gst-plugins-base/gst-plugins-base-1.28.9.tar.xz
PipeWire 1.6.11 https://github.com/PipeWire/pipewire/tree/1.6.11
AppImage type2 runtime https://github.com/AppImage/type2-runtime/tree/fedfad5
Runtime binary SHA-256 617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46
""")
print(f"Staged {len(elfs)} ELF files from {len(packages)} Ubuntu packages.")
