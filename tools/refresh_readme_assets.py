"""Regenerate README assets: crop the viewer shots, grab the current panels.

- poly-mesh-*.png: crop to the 3D viewer area only (the old full-window
  shots carried a stale Italian UI around the render).
- mesher-selector.png / boundary-layers.png: fresh grabs of the CURRENT
  ParamsPanel groups (English, current buttons/fields) via offscreen Qt.
"""
import os
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Davide Valoroso\cfmesh-autogui")
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ASSETS = REPO / "docs" / "assets"


def crop_viewer() -> None:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    # viewer area: right of the left dock, below the ribbon/toolbar,
    # above the log dock (measured on the 1785x918 originals).
    rect = QRect(268, 98, 1140, 748)
    for name in ("poly-mesh-overview.png", "poly-mesh-closeup.png"):
        img = QImage(str(ASSETS / name))
        if img.isNull():
            print(f"WARN: could not load {name}")
            continue
        cropped = img.copy(rect)
        cropped.save(str(ASSETS / name), "PNG")
        print(f"cropped {name}: {cropped.width()}x{cropped.height()}")


def grab_panels() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from polyfoammesh.gui.params_panel import ParamsPanel

    panel = ParamsPanel()
    panel.resize(420, 1200)

    def _settle(tab_widget) -> None:
        for _ in range(4):
            app.processEvents()
        page = tab_widget.currentWidget()
        if page is not None and page.layout() is not None:
            page.layout().activate()

    # Advanced tab FIRST, before show(): a tab page that is never current
    # offscreen never gets laid out, and grab() would return a collapsed
    # pixmap (measured: 308x53 for a 15-row form).
    idx = panel._tabs.indexOf(panel._adv_tab)
    if idx >= 0:
        panel._tabs.setCurrentIndex(idx)
    panel.show()
    _settle(panel._tabs)
    # The BL form is hidden until the master checkbox is ticked
    # (setVisible(False) in the panel; the toggle reveals it) - tick it and
    # force the layout chain to recompute so the grab shows the full panel.
    panel._bl_checkbox.setChecked(True)
    panel._bl_form_widget.setVisible(True)
    for w in (panel._bl_form_widget, panel._bl_group):
        lay = w.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
    _settle(panel._tabs)
    grp = panel._bl_group
    # The group inside a never-current tab keeps a collapsed geometry, so
    # grab the FULL laid-out page and crop to the group's real rectangle.
    from PySide6.QtCore import QPoint, QRect, QSize

    page = panel._adv_tab
    page_pix = page.grab()
    pos = grp.mapTo(page, QPoint(0, 0))
    hint = grp.sizeHint()
    w = max(grp.width(), hint.width())
    h = max(grp.height(), hint.height())
    rect = QRect(pos, QSize(w, h)).intersected(QRect(0, 0, page_pix.width(), page_pix.height()))
    bl = page_pix.copy(rect)
    bl.save(str(ASSETS / "boundary-layers.png"), "PNG")
    print(f"boundary-layers.png: {bl.width()}x{bl.height()} (page {page_pix.width()}x{page_pix.height()})")

    # Then the Mesh tab for the mesher selector (tab order: Geometry,
    # Mesh, Advanced — the Mesh page is index 1).
    panel._tabs.setCurrentIndex(1)
    _settle(panel._tabs)
    mgrp = panel._mesher_group
    mgrp.resize(mgrp.sizeHint())
    mesher = mgrp.grab()
    mesher.save(str(ASSETS / "mesher-selector.png"), "PNG")
    print(f"mesher-selector.png: {mesher.width()}x{mesher.height()}")
    panel.close()


if __name__ == "__main__":
    crop_viewer()
    grab_panels()
