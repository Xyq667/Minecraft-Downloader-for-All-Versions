"""Build-time asset generation: app icon and a fallback manifest snapshot."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def make_icon() -> None:
    scale = 4
    size = 512 * scale
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def p(value: float) -> int:
        return int(round(value * scale))

    # Rounded dark tile.
    draw.rounded_rectangle(
        (p(6), p(6), p(506), p(506)),
        radius=p(108),
        fill="#0d151f",
        outline="#233140",
        width=p(5),
    )
    # Soft green glow ring.
    draw.rounded_rectangle(
        (p(22), p(22), p(490), p(490)),
        radius=p(94),
        outline="#173b2a",
        width=p(4),
    )

    # Isometric grass block.
    top = [(p(256), p(82)), (p(412), p(172)), (p(256), p(262)), (p(100), p(172))]
    left = [(p(100), p(172)), (p(256), p(262)), (p(256), p(412)), (p(100), p(322))]
    right = [(p(412), p(172)), (p(256), p(262)), (p(256), p(412)), (p(412), p(322))]
    draw.polygon(left, fill="#7c5238")
    draw.polygon(right, fill="#5f3d2a")
    draw.polygon(top, fill="#53e38a")
    # Grass fringe on both sides.
    draw.polygon(
        [(p(100), p(172)), (p(256), p(262)), (p(256), p(302)), (p(100), p(212))],
        fill="#38bd6d",
    )
    draw.polygon(
        [(p(412), p(172)), (p(256), p(262)), (p(256), p(302)), (p(412), p(212))],
        fill="#2e9f5a",
    )
    # Pixel grass blades on top.
    for x, y, w, h in ((166, 158, 12, 20), (196, 145, 10, 18), (300, 150, 10, 18), (330, 165, 12, 20)):
        draw.rounded_rectangle((p(x), p(y), p(x + w), p(y + h)), radius=p(3), fill="#86f5b0")
    # Download arrow badge.
    cx, cy = p(344), p(342)
    draw.ellipse(
        (cx - p(66), cy - p(66), cx + p(66), cy + p(66)),
        fill="#0b1118",
        outline="#46df87",
        width=p(8),
    )
    draw.line((cx, cy - p(35), cx, cy + p(8)), fill="#eafff2", width=p(15))
    draw.polygon(
        [
            (cx - p(30), cy - p(4)),
            (cx + p(30), cy - p(4)),
            (cx, cy + p(34)),
        ],
        fill="#eafff2",
    )
    draw.line((cx - p(30), cy + p(44), cx + p(30), cy + p(44)), fill="#eafff2", width=p(12))

    image = image.resize((512, 512), Image.Resampling.LANCZOS)
    image.putalpha(rounded_mask(512, 108))
    image.save(ASSETS / "icon.png")
    image.save(
        ASSETS / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("wrote", ASSETS / "icon.png", "and", ASSETS / "icon.ico")


def make_manifest_snapshot() -> None:
    url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    request = urllib.request.Request(url, headers={"User-Agent": "MCVersionDownloader-build/1.0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        data = response.read()
    json.loads(data.decode("utf-8"))
    (ASSETS / "version_manifest_snapshot.json").write_bytes(data)
    print("wrote", ASSETS / "version_manifest_snapshot.json", len(data), "bytes")


if __name__ == "__main__":
    make_icon()
    make_manifest_snapshot()
