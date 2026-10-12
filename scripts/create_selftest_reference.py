#!/usr/bin/env python3
"""Generate the small synthetic reference image bundled for H3 self-tests."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def create_png(path: Path, size: int = 256) -> None:
    rows = []
    center = (size - 1) / 2
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            distance = ((x - center) ** 2 + (y - center) ** 2) ** 0.5 / center
            if distance < 0.52:
                red, green, blue = 255, int(105 + 100 * (y / size)), 75
            else:
                red, green, blue = int(20 + 30 * (x / size)), int(38 + 50 * (y / size)), 82
            if abs(distance - 0.52) < 0.018:
                red, green, blue = 250, 224, 145
            row.extend((red, green, blue))
        rows.append(bytes(row))

    raw = b"".join(rows)
    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, level=9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/selftest-reference.png")
    create_png(destination)
    print(f"created bundled selftest image: {destination}")
