"""Build the current addon ZIP and two experimental legacy ZIPs from one Lua file."""

import argparse
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
NAME = "SpokenMediaPause"

# These clients read only the unsuffixed TOC and require a single interface number.
LEGACY_TARGETS = (
    ("WotLK-3.3.5a", 30300, "Spoken"),
    ("Vanilla-1.12-SoftSpoken", 11200, "SoftSpoken"),
)


def version_of(manifest):
    """Read the release version used in the ZIP names."""
    match = re.search(r"^## Version: ([0-9]+\.[0-9]+\.[0-9]+)$", manifest, re.MULTILINE)
    if not match:
        raise ValueError("Expected a numeric addon version in the TOC")
    return match.group(1)


def write_zip(path, source, manifests):
    """Write one installable AddOns folder with a shared Lua file and its load manifests."""
    entries = {f"{NAME}.lua": source, **manifests}
    with ZipFile(path, "w") as archive:
        for filename, content in sorted(entries.items()):
            # A fixed timestamp and mode keep identical inputs reproducible.
            entry = ZipInfo(f"{NAME}/{filename}")
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content, compress_type=ZIP_DEFLATED)


def main():
    """Write the three addon packages to the selected output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=ROOT / "dist")
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)

    folder = ROOT / "addon" / NAME
    source = (folder / f"{NAME}.lua").read_bytes()
    manifests = {path.name: path.read_text() for path in folder.glob("*.toc")}
    default_name = f"{NAME}.toc"
    default_manifest = manifests[default_name]
    version = version_of(default_manifest)
    if any(version_of(manifest) != version for manifest in manifests.values()):
        raise ValueError("Addon TOCs must agree on the release version")

    current_zip = output / f"{NAME}-{version}.zip"
    write_zip(current_zip, source, manifests)
    print(current_zip)
    for suffix, interface, dependency in LEGACY_TARGETS:
        manifest = re.sub(r"^## Interface: .*$", f"## Interface: {interface}", default_manifest, flags=re.MULTILINE)
        manifest = re.sub(r"^## Dependencies: .*$", f"## Dependencies: {dependency}", manifest, flags=re.MULTILINE)
        legacy_zip = output / f"{NAME}-{version}-{suffix}.zip"
        write_zip(legacy_zip, source, {default_name: manifest})
        print(legacy_zip)


if __name__ == "__main__":
    main()
