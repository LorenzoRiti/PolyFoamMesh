"""Dialog: build a virtual wind tunnel around the loaded object.

Every criterion of :class:`polyfoammesh.core.wind_tunnel.TunnelSpec` is an
editable field, pre-filled with the default practice; the resulting tunnel
size and frontal blockage are shown live before anything is built.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from polyfoammesh.core.wind_tunnel import TunnelSpec, compute_tunnel

_DIRS = [("+X", 0, 1), ("-X", 0, -1), ("+Y", 1, 1), ("-Y", 1, -1),
         ("+Z", 2, 1), ("-Z", 2, -1)]
_UPS = [("Z", 2), ("Y", 1), ("X", 0)]


class WindTunnelDialog(QDialog):
    """Collects a :class:`TunnelSpec` for an object bounding box."""

    def __init__(self, obj_lo, obj_hi, unit_label: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create wind tunnel")
        self._lo, self._hi = tuple(obj_lo), tuple(obj_hi)
        self._unit = unit_label
        lay = QVBoxLayout(self)
        intro = QLabel(
            "The loaded geometry becomes the OBJECT; the fluid is the box "
            "around it. Distances are multiples of the object size "
            "(L = length along the flow, H = height, D = max(height, width)).")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self._dir = QComboBox()
        for label, _a, _s in _DIRS:
            self._dir.addItem(f"Flow towards {label}")
        self._up = QComboBox()
        for label, _a in _UPS:
            self._up.addItem(f"Up is {label}")
        self._ground = QCheckBox("Object rests on the ground (car, building)")

        def _spin(val, lo, hi, step, suffix):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(2)
            s.setSingleStep(step)
            s.setValue(val)
            s.setSuffix(suffix)
            return s

        d = TunnelSpec()
        self._up_L = _spin(d.upstream_L, 0.5, 50.0, 0.5, " L")
        self._down_L = _spin(d.downstream_L, 1.0, 100.0, 1.0, " L")
        self._side_D = _spin(d.side_D, 0.5, 50.0, 0.5, " D")
        self._top_H = _spin(d.top_H, 0.5, 50.0, 0.5, " H")
        self._block = _spin(100.0 * d.max_blockage, 0.5, 20.0, 0.5, " %")
        form.addRow("Flow direction", self._dir)
        form.addRow("Vertical axis", self._up)
        form.addRow("", self._ground)
        form.addRow("Upstream", self._up_L)
        form.addRow("Downstream", self._down_L)
        form.addRow("Each side", self._side_D)
        form.addRow("Above", self._top_H)
        form.addRow("Max frontal blockage", self._block)
        lay.addLayout(form)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        lay.addWidget(self._summary)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)
        for w in (self._dir, self._up):
            w.currentIndexChanged.connect(self._refresh)
        self._ground.toggled.connect(self._refresh)
        for w in (self._up_L, self._down_L, self._side_D, self._top_H,
                  self._block):
            w.valueChanged.connect(self._refresh)
        self._refresh()

    def spec(self) -> TunnelSpec:
        _label, axis, sign = _DIRS[self._dir.currentIndex()]
        return TunnelSpec(
            flow_axis=axis, flow_sign=sign,
            up_axis=_UPS[self._up.currentIndex()][1],
            on_ground=self._ground.isChecked(),
            upstream_L=self._up_L.value(), downstream_L=self._down_L.value(),
            side_D=self._side_D.value(), top_H=self._top_H.value(),
            max_blockage=self._block.value() / 100.0,
        )

    def _refresh(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        try:
            t = compute_tunnel(self._lo, self._hi, self.spec())
        except ValueError as exc:
            self._summary.setText(f"<b>Not valid:</b> {exc}")
            ok.setEnabled(False)
            return
        ext = [t.hi[i] - t.lo[i] for i in range(3)]
        u = f" {self._unit}" if self._unit else ""
        txt = (f"Object: L {t.L:.4g} (along the flow) x H {t.H:.4g} "
               f"(vertical) x W {t.W:.4g}{u}.  Tunnel {ext[0]:.4g} x "
               f"{ext[1]:.4g} x {ext[2]:.4g}{u}, frontal blockage "
               f"{100 * t.blockage:.2f} %.  If H looks like the wrong side "
               "(a wing's span, a car's width) change the vertical axis.")
        if t.widened:
            txt += " Sides and top were widened to respect the blockage limit."
        self._summary.setText(txt)
        ok.setEnabled(True)
