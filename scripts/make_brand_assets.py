"""Render the Solar Sanity brand icon.

The mark is a sun whose disc holds a checkmark: the product looks at your solar
data and tells you whether it checks out. Two shapes, one colour each, no
gradient — it has to stay legible at the 24-32px Home Assistant actually draws
it at, which rules out anything with fine detail.

The check is drawn in a dark slate rather than knocked out to transparency, so
the mark reads identically on light and dark dashboards instead of depending on
whatever is behind it.

Rendered at 4x and downsampled for antialiasing, because Pillow has no native
antialiased polygon fill.

Home Assistant serves these from the integration's own ``brand/`` directory as
of 2026.3 — local images take priority over the brands CDN and no manifest key
is involved. The brands repository no longer accepts custom-integration icons.

Run: python scripts/make_brand_assets.py [output_dir]
"""

from __future__ import annotations

import math
import pathlib
import sys

from PIL import Image, ImageDraw

#: Supersampling factor. 4x is plenty for shapes this simple.
SS = 4

#: Solar amber. Saturated enough to hold up against both a white card and a
#: near-black one, which the HA integrations page will do.
AMBER = (233, 147, 11, 255)

#: Deep slate for the check. Near-black would look harsh against the amber.
SLATE = (26, 34, 43, 255)

#: Eight rays. More looks busy at small sizes; four looks like a compass.
RAY_COUNT = 8


def _draw(size: int) -> Image.Image:
    """Render the mark at ``size`` px, square, with a transparent background."""
    canvas = size * SS
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    centre = canvas / 2
    # Trimmed: the ray tips reach almost to the edge, so the file carries the
    # minimum empty border the brands guidelines ask for.
    ray_tip = canvas * 0.485
    ray_base = canvas * 0.345
    disc_radius = canvas * 0.285
    ray_half_width = math.radians(9)

    for index in range(RAY_COUNT):
        angle = (2 * math.pi / RAY_COUNT) * index - math.pi / 2
        tip = (centre + ray_tip * math.cos(angle), centre + ray_tip * math.sin(angle))
        left = (
            centre + ray_base * math.cos(angle - ray_half_width),
            centre + ray_base * math.sin(angle - ray_half_width),
        )
        right = (
            centre + ray_base * math.cos(angle + ray_half_width),
            centre + ray_base * math.sin(angle + ray_half_width),
        )
        draw.polygon([tip, left, right], fill=AMBER)

    draw.ellipse(
        [
            centre - disc_radius,
            centre - disc_radius,
            centre + disc_radius,
            centre + disc_radius,
        ],
        fill=AMBER,
    )

    # The checkmark. Deliberately heavy — a thin tick disappears at 24px.
    stroke = canvas * 0.072
    points = [
        (centre - disc_radius * 0.52, centre + disc_radius * 0.05),
        (centre - disc_radius * 0.14, centre + disc_radius * 0.44),
        (centre + disc_radius * 0.56, centre - disc_radius * 0.40),
    ]
    draw.line(points, fill=SLATE, width=int(stroke), joint="curve")
    # Round the ends so the tick does not look chopped off.
    for point in (points[0], points[2]):
        draw.ellipse(
            [
                point[0] - stroke / 2,
                point[1] - stroke / 2,
                point[0] + stroke / 2,
                point[1] + stroke / 2,
            ],
            fill=SLATE,
        )

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    default = "custom_components/solar_sanity/brand"
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else default)
    out.mkdir(parents=True, exist_ok=True)

    for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
        image = _draw(size)
        image.save(
            out / name,
            "PNG",
            optimize=True,
            interlace=True,  # brands prefers interlaced
        )
        print(f"{out / name}: {image.size[0]}x{image.size[1]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
