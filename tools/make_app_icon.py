#!/usr/bin/env python3
"""Regenerate src/polyfoammesh/assets/app.ico from the in-app branding logo.

The runtime window icon is drawn programmatically (gui/branding.py); this
script renders the SAME logo to a multi-size Windows .ico used as the
executable's embedded icon (PolyFoamMesh.spec `icon=`) and by the
installer (inno_setup.iss SetupIconFile).  Without it PyInstaller embeds
its own default icon, which is what showed up on the taskbar instead of
the app logo (user report).

Usage (from the repo root):
    QT_QPA_PLATFORM=offscreen python tools/make_app_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841

    from polyfoammesh.gui.branding import make_logo_pixmap

    out = REPO / "src" / "polyfoammesh" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    ico_path = out / "app.ico"

    payloads: list[tuple[int, bytes]] = []
    for s in SIZES:
        pix = make_logo_pixmap(s)
        tmp = out / f"_tmp_{s}.png"
        pix.save(str(tmp), "PNG")
        payloads.append((s, tmp.read_bytes()))
        tmp.unlink()

    # ICO container: 6-byte header + 16-byte directory entry per image +
    # PNG payloads (PNG-in-ICO is supported since Vista; Qt/Windows read it).
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    entries = b""
    blobs = b""
    for s, data in payloads:
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    ico_path.write_bytes(header + entries + blobs)
    print(f"wrote {ico_path} ({ico_path.stat().st_size} bytes, {len(payloads)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
