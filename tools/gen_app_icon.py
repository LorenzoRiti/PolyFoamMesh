"""Generate installer/assets/icon.ico from the app's programmatic logo
(branding.py).

Run once (or whenever the logo design changes) to produce a real .ico file
for PyInstaller's EXE(icon=...) and Inno Setup's SetupIconFile — the logo
itself is drawn at runtime with QPainter (no static image assets), but the
.exe file and its shortcuts need an embedded icon that exists before the
app ever starts.

Round-trips each size through PNG bytes (QPixmap.save -> PIL.Image.open)
instead of touching the raw pixel buffer directly: a hand-rolled
QImage.bits() -> PIL.Image.frombuffer conversion silently mishandled the
alpha channel (transparent corners came back as opaque black in the
Windows icon viewer, and outer rounded corners were left unclipped) —
going through the PNG codec on both ends guarantees correct alpha.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QBuffer, QIODevice
from PySide6.QtGui import QPixmap
from polyfoammesh.gui.branding import _draw_logo_on
from PIL import Image

app = QApplication.instance() or QApplication([])

sizes = [16, 24, 32, 48, 64, 128, 256]
pil_images = []
for s in sizes:
    pix = QPixmap(s, s)
    # fill(0) is NOT transparent in PySide6 (produces opaque black) —
    # Qt.transparent is what branding.py's own make_app_icon() uses.
    pix.fill(Qt.transparent)
    _draw_logo_on(pix, dark=False)

    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pix.save(buf, "PNG")
    png_bytes = bytes(buf.data())
    buf.close()

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    assert img.size == (s, s), f"size mismatch: wanted {s}, got {img.size}"
    pil_images.append(img)

out_dir = Path(__file__).resolve().parent.parent / "installer" / "assets"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "icon.ico"

# Largest image first as the "base" — Pillow's ICO writer uses its mode/
# palette as the template and embeds the rest via append_images.
pil_images_desc = list(reversed(pil_images))
pil_images_desc[0].save(
    str(out_path), format="ICO",
    sizes=[img.size for img in pil_images_desc],
    append_images=pil_images_desc[1:],
)
print(f"Wrote {out_path} ({out_path.stat().st_size} bytes, sizes={sizes})")
