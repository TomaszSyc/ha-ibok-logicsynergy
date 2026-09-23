"""Brand images are what Home Assistant shows on the integrations page.

Home Assistant serves them from ``<integration>/brand/`` and only looks there
when that directory exists, so a misplaced file means the generic placeholder
silently comes back instead.
"""

import struct
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1] / "custom_components" / "ibok" / "brand"


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def test_brand_icons_have_required_sizes() -> None:
    for name, expected in (("icon.png", 256), ("icon@2x.png", 512)):
        path = BRAND / name
        assert path.is_file(), f"missing {path}"
        assert _png_size(path) == (expected, expected), path.name
