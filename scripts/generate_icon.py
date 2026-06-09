from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "sonarqube-profile-creator-logo.png"
PNG_OUT = ROOT / "assets" / "app_icon.png"
ICO_OUT = ROOT / "assets" / "app_icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> int:
    source = Image.open(SOURCE).convert("RGBA")
    source.save(PNG_OUT)
    source.save(ICO_OUT, sizes=[(size, size) for size in SIZES])
    print(PNG_OUT)
    print(ICO_OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
