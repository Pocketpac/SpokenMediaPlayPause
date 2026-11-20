"""Render scaled marker fixtures from the addon's real Lua drawing commands."""

import argparse
from collections import defaultdict
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CROP = (200, 160)
SAMPLES = 8


def rectangles():
    """Read the layer, geometry, and color of each visible marker region."""
    drawing = subprocess.check_output(
        ["lua5.1", "tests/addon/marker.lua", "--frames"], cwd=ROOT, text=True
    )
    frames = defaultdict(list)
    for line in drawing.splitlines():
        name, layer, x, y, width, height, red, green, blue = line.split("\t")
        frames[name].append((layer, tuple(map(float, (x, y, width, height))), tuple(map(int, (red, green, blue)))))
    return frames


def render(art, scene, regions, scale, phase):
    """Composite the badge at a fractional scale using coverage antialiasing."""
    size = round(28 * scale * SAMPLES)
    badge = Image.new("RGBA", (size, size))
    offset_x, offset_y = phase
    if art is not None:
        edge = round(26 * scale * SAMPLES)
        badge.alpha_composite(art.resize((edge, edge)), (round(offset_x * SAMPLES), round(offset_y * SAMPLES)))
    draw = ImageDraw.Draw(badge)
    for _, (x, y, width, height), color in sorted(regions, key=lambda region: region[0] == "OVERLAY"):
        left, top = round((x * scale + offset_x) * SAMPLES), round((y * scale + offset_y) * SAMPLES)
        right = round(((x + width) * scale + offset_x) * SAMPLES) - 1
        bottom = round(((y + height) * scale + offset_y) * SAMPLES) - 1
        draw.rectangle((left, top, right, bottom), fill=(*color, 255))
    edge = round(size / SAMPLES)
    frame = scene.copy()
    frame.alpha_composite(badge.resize((edge, edge), Image.Resampling.BOX), (12, 12))
    return frame


def main():
    """Write raw BGRA crops and expected readings for both desktop decoders."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    atlas = Image.open(ROOT / "addon/SpokenMediaPause/BrassBadge.tga").convert("RGBA")
    art = {state: atlas.crop((index * 64, 0, (index + 1) * 64, 64))
           for index, state in enumerate(("idle", "narrating", "off"))}
    scene = Image.open(ROOT / "assets/in-game-marker.png").crop((74, 32, 274, 192)).convert("RGBA")
    cases = []

    def write(name, frame, state, heartbeat):
        """Write one independent crop and record its expected decoder result."""
        filename = f"{name}.bgra"
        (output / filename).write_bytes(frame.tobytes("raw", "BGRA"))
        cases.append(f"{filename}\t{state}\t{heartbeat}\n")

    for name, regions in rectangles().items():
        style, state, clock = name.split("-")
        for scale in (0.8, 1, 1.15, 1.333, 1.6, 1.75, 2, 2.5):
            for phase_index, phase in enumerate(((0, 0), (0.3, 0.65), (0.7, 0.35))):
                frame = render(art[state] if style == "brass" else None, scene, regions, scale, phase)
                write(f"{name}-{scale}-{phase_index}", frame, state, int(clock == "light"))
        if style == "brass":
            for delta in (-7, 7):
                frame = render(art[state], scene, regions, 1.6, (0.2, 0.3))
                lut = [min(255, max(0, level + delta)) for level in range(256)]
                write(f"{name}-level{delta}", frame.point(lut * 3 + list(range(256))), state, int(clock == "light"))
            # Probe small channel shifts independently of overall brightness.
            frame = render(art[state], scene, regions, 1.15, (0.7, 0.35))
            for delta in (-2, 2):
                lut = [min(255, max(0, level + shift))
                       for shift in (delta, -delta, delta, 0) for level in range(256)]
                write(f"{name}-tint{delta}", frame.point(lut), state, int(clock == "light"))
            frame = render(art[state], scene, regions, 1.6, (0, 0))
            ImageDraw.Draw(frame).rectangle((25, 46, 39, 56), fill=(81, 125, 43, 255))
            write(f"{name}-occluded", frame, "-", 0)
    write("scenery", scene, "-", 0)
    (output / "expected.tsv").write_text("".join(cases), encoding="ascii")
    print(f"Wrote {len(cases)} fixture crops.")


if __name__ == "__main__":
    main()
