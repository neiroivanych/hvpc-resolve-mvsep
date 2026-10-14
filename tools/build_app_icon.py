"""Convert the supplied logo to Windows icon sizes without changing its artwork.

Maintainer-only tool; requires Pillow. Neither setup nor the app needs Pillow.
The original JPEG and the generated ICO are committed together.
"""
from pathlib import Path

from PIL import Image


def main():
    root = Path(__file__).resolve().parents[1]
    with Image.open(root / 'assets/hvpc-logo.jpg') as original:
        original.convert('RGBA').save(
            root / 'src/resolve_mvsep/app.ico', format='ICO', bitmap_format='bmp',
            sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
        )


if __name__ == '__main__':
    main()
