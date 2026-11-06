#!/usr/bin/env python3
"""Draw hamdash's icon and write every file that carries it.

    python tools/make_icon.py

An antenna mast on the board's own background, with the signal radiating
from its top: the inner wave in the dial amber (--accent), the outer in the
receive teal (--signal) - transmit and receive, the two things a station
does. At 16-24 px it is redrawn with one bolder wave a side, because two
thin arcs at that size are a grey smudge.

Drawn here rather than in an editor so it can be changed and rebuilt by
anyone: shapes are signed distance fields, so every edge is anti-aliased at
every size with no supersampling, and only numpy is needed. PNG and ICO are
written directly - both are simple containers - so there is no image
library to install. Outputs:

    hamdash/static/icon/hamdash.ico     Windows: the exe, the installer, shortcuts
    hamdash/static/icon/hamdash.icns    macOS: the .app
    hamdash/static/icon/hamdash-512.png  window icon (Linux), large uses
    hamdash/static/icon/hamdash-192.png  phone home screen
    hamdash/static/icon/favicon-64.png   the board's browser tab
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "hamdash" / "static" / "icon"

# The board's own colours (static/css/app.css, the default theme).
BG_TOP = (0x17, 0x24, 0x2d)
BG = (0x0d, 0x14, 0x19)
RULE = (0x26, 0x36, 0x3f)
ACCENT = (0xe9, 0xa1, 0x3b)
SIGNAL = (0x56, 0xb6, 0xa8)

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> np.ndarray:
    """RGBA, size x size, uint8."""
    n = size
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / n          # pixel centres, 0..1
    px = 1.0 / n                                    # one pixel in units

    def cover(sdf):                                 # anti-aliased coverage
        return np.clip(0.5 - sdf / px, 0.0, 1.0)

    def rounded_rect(cx, cy, hw, hh, r):
        qx = np.abs(x - cx) - (hw - r)
        qy = np.abs(y - cy) - (hh - r)
        return (np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
                + np.minimum(np.maximum(qx, qy), 0) - r)

    def circle(cx, cy, r):
        return np.hypot(x - cx, y - cy) - r

    def capsule(ax, ay, bx, by, r):
        pax, pay, bax, bay = x - ax, y - ay, bx - ax, by - ay
        h = np.clip((pax * bax + pay * bay) / (bax * bax + bay * bay), 0, 1)
        return np.hypot(pax - bax * h, pay - bay * h) - r

    def arc(cx, cy, r, w, a0, a1):
        """A stroke of width w along radius r from angle a0 to a1 (degrees,
        0 = right, counter-clockwise in screen terms), round caps."""
        dx, dy = x - cx, cy - y                     # y up for the angles
        ang = np.degrees(np.arctan2(dy, dx))
        mid, half = (a0 + a1) / 2, (a1 - a0) / 2
        rel = (ang - mid + 180) % 360 - 180
        on = np.abs(rel) <= half
        d_ring = np.abs(np.hypot(dx, dy) - r) - w / 2
        ends = []
        for a in (a0, a1):
            ex, ey = cx + r * np.cos(np.radians(a)), cy - r * np.sin(np.radians(a))
            ends.append(np.hypot(x - ex, y - ey) - w / 2)
        return np.where(on, d_ring, np.minimum(*ends))

    rgb = np.zeros((n, n, 3))
    alpha = np.zeros((n, n))

    def paint(sdf, colour, a=1.0):
        nonlocal rgb, alpha
        c = cover(sdf) * a
        rgb = rgb * (1 - c[..., None]) + np.array(colour)[None, None, :] / 255 * c[..., None]
        alpha = alpha + c * (1 - alpha)

    # The tile: the board's background, a touch lighter at the top, with
    # its rule colour as an edge so it holds on a dark taskbar too.
    tile = rounded_rect(0.5, 0.5, 0.47, 0.47, 0.20)
    grad = np.clip((y - 0.03) / 0.94, 0, 1)[..., None]
    bg = (np.array(BG_TOP) * (1 - grad) + np.array(BG) * grad) / 255
    c = cover(tile)
    rgb = bg * c[..., None]
    alpha = c
    paint(np.abs(tile + 0.012) - 0.012, RULE)

    small = size <= 24
    top = (0.5, 0.33)                               # the radiator's tip
    stroke = 0.075 if small else 0.052

    # The mast: an A-frame tower, a feed line up the middle, the tip lit.
    paint(capsule(0.5, 0.36, 0.33, 0.83, stroke / 2), ACCENT)
    paint(capsule(0.5, 0.36, 0.67, 0.83, stroke / 2), ACCENT)
    if not small:
        paint(capsule(0.5, 0.40, 0.5, 0.83, stroke * 0.35), ACCENT, 0.85)
        paint(capsule(0.415, 0.60, 0.585, 0.60, stroke * 0.35), ACCENT, 0.85)
    paint(circle(*top, 0.085 if small else 0.062), ACCENT)

    # The signal, both ways.
    waves = ([(0.22, 0.085, ACCENT)] if small else
             [(0.165, stroke, ACCENT), (0.27, stroke, SIGNAL)])
    for r, w, colour in waves:
        for a0, a1 in ((-38, 38), (142, 218)):
            paint(arc(*top, r, w, a0, a1), colour)

    out = np.dstack([np.clip(rgb / np.maximum(alpha, 1e-9)[..., None], 0, 1),
                     alpha])
    return (out * 255 + 0.5).astype(np.uint8)


def png(rgba: np.ndarray) -> bytes:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images: dict) -> bytes:
    """PNG-compressed entries, which Windows has read since Vista."""
    sizes = sorted(images)
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for s in sizes:
        data = images[s]
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        blobs += data
    return head + entries + blobs


# icns entry types that hold a PNG, by pixel size: the plain kinds, then
# the @2x ones a Retina display asks for, which are the same pixels.
ICNS_TYPES = ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128),
              (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024),
              (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512))


def icns(images: dict) -> bytes:
    """Apple's container: 'icns', the total length, then (type, length,
    PNG) entries - big-endian, lengths counting their own 8-byte header."""
    body = b""
    for kind, size in ICNS_TYPES:
        data = images[size]
        body += kind + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    drawn = {s: png(render(s)) for s in set(ICO_SIZES) | {s for _k, s in ICNS_TYPES}}
    (OUT / "hamdash.ico").write_bytes(ico({s: drawn[s] for s in ICO_SIZES}))
    (OUT / "hamdash.icns").write_bytes(icns(drawn))
    for name, s in (("hamdash-512.png", 512), ("hamdash-192.png", 192),
                    ("favicon-64.png", 64)):
        (OUT / name).write_bytes(png(render(s)))
    for p in sorted(OUT.iterdir()):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
