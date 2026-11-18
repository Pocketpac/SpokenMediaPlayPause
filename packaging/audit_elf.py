from collections import Counter
from pathlib import Path
import re
import subprocess
import sys

def readelf(flag, path):
    return subprocess.check_output(["readelf", flag, path], text=True)

root, runtime = Path(sys.argv[1]), Path(sys.argv[2])
counts = Counter()
maxima = []
targets = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
for path in [runtime, *sorted(targets)]:
    with path.open("rb") as file:
        if file.read(4) != b"\x7fELF":
            continue
    header = readelf("-Wh", path)
    assert "ELF64" in header and "Advanced Micro Devices X86-64" in header, path
    versions = readelf("-WV", path).partition("Version needs section")[2]
    glibc_versions = set(re.findall(r"Name: GLIBC_([^\s]+)", versions))
    assert all(re.fullmatch(r"\d+(?:\.\d+)+", tag) for tag in glibc_versions), (path, glibc_versions)
    numeric = [tuple(map(int, tag.split("."))) for tag in glibc_versions]
    version = max(numeric, default=())
    assert version <= (2, 35), (path, version)
    if path == runtime:
        assert not version and "INTERP" not in readelf("-Wl", path), "Expected static AppImage runtime"
    else:
        label = ".".join(map(str, version)) or "none"
        counts[label] += 1
        maxima.append((version, path.relative_to(root)))

maximum = max(version for version, _ in maxima)
print(f"{len(maxima)} packaged x86-64 ELF files, maximum GLIBC_{'.'.join(map(str, maximum))}.")
print("Static x86-64 AppImage launcher has no glibc requirement.")
print("Counts by maximum required version:")
for key, count in sorted(counts.items()):
    print(f"  {key}: {count}")
print("At the maximum:", ", ".join(str(path) for version, path in maxima if version == maximum))
