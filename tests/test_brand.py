"""The brand icon that HACS and Home Assistant show: square PNGs with a transparent background."""

from pathlib import Path

BRAND = Path(__file__).parents[1] / "custom_components" / "kryptex" / "brand"


def png_info(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"), data[25]


def test_icons_are_square_rgba_pngs():
    # Colour type 6 = RGBA, so the background can be transparent.
    assert png_info(BRAND / "icon.png") == (256, 256, 6)
    assert png_info(BRAND / "icon@2x.png") == (512, 512, 6)
