# Component Guidelines — PolyFoamMesh Design System

> Commercial-grade usage rules for every widget. **All values come from
> `design_tokens.py` — never hardcode colors / spacing / font sizes in widget code.**

---

## 1. Buttons (`QPushButton`)

| Variant     | When to use                                | Implementation |
|-------------|--------------------------------------------|----------------|
| **Default** | Primary action on a screen (e.g. "Generate Mesh") | `setDefault(True)` — auto-themed with accent color |
| **Secondary** | Non-primary actions ("Load...", "Reset", "Cancel") | Plain `QPushButton` (auto-themed) |
| **Destructive** | Delete / clear all (e.g. "Reset All") | Same as secondary, but always show a confirmation `QMessageBox` first |
| **Cancel / stop** | Mid-process abort (e.g. meshing) | `set_meshing_state(True)` helper — auto-styles to red, label becomes "Cancel" |
| **Icon button** | Toolbar | Set 16×16 icon, square 28×28 px, no text |

**Sizes (from `design_tokens.component.button`):**
- `BUTTON_HEIGHT_SM = 24` (toolbar)
- `BUTTON_HEIGHT_MD = 32` (most form buttons)
- `BUTTON_HEIGHT_LG = 40` (primary CTA in the main action area)
- `BUTTON_MIN_WIDTH = 64` (avoids weirdly narrow buttons)
- `BUTTON_PADDING_X = 12`

**Rules:**
1. Always have a tooltip (`setToolTip`) on every button.
2. Use the keyword style: `verb` not `verb...` ("Generate Mesh" not "Generate Mesh...").
3. Disabling (`setEnabled(False)`) must never silently fail — pair with a status-bar message.
4. Default button = the one triggered by Enter on a form.

---

## 2. Inputs (`QLineEdit`, `QSpinBox`, `QDoubleSpinBox`)

| Field type       | Widget                | Validation                      |
|------------------|-----------------------|---------------------------------|
| Integer number   | `QSpinBox`            | `setRange(min, max)` + `setSuffix` |
| Float number     | `QDoubleSpinBox`      | `setDecimals(N)` + `setSuffix(" m")` |
| Short text       | `QLineEdit`           | `setMaxLength(N)` if bounded    |
| Multi-line       | `QPlainTextEdit`      | n/a                             |
| Single choice    | `QComboBox`           | n/a                             |

**Sizes:**
- `INPUT_HEIGHT = 32` (consistent baseline)
- `INPUT_PADDING_X = 10`
- `INPUT_BORDER = 1` (theme-driven)

**Rules:**
1. **Always** set suffix/unit when applicable (`" m"`, `"%"`, etc.).
2. **Always** show units in the label: `"Max Cell Size:"` not `"Max Cell"`.
3. Tooltip on every input: `<field> [<unit>]. <constraint>`.
4. Disabled inputs must be visibly distinct (QSS handles this — `bgSubtle`).

---

## 3. Selection controls (`QCheckBox`, `QRadioButton`)

**Rules:**
1. Checkbox = independent toggle. Radio = exactly one of N.
2. Label is sentence case, no period.
3. The first letter of the label is the mnemonic (`QShortcut`-friendly).
4. Use `setChecked` in code; never assume the initial value is `False`.

---

## 4. Group panels (`QGroupBox`)

| Use case         | Style                                    |
|------------------|------------------------------------------|
| Tool section     | `QGroupBox("Mesh Parameters")` with form layout inside |
| Settings block   | `QGroupBox("Boundary Layers")` with toggle inside |
| Status group     | `QGroupBox("Mesh Quality:")` (status-panel pattern) |

**Rules:**
1. The title is the **short** noun, not a full sentence.
2. Padding inside is `PANEL_PADDING = 12` (auto via QSS).
3. Collapsible groups: use `setCheckable(True)` for optional sections (e.g. BL).
4. Avoid nesting more than 2 levels deep — that hurts scannability.

---

## 5. Lists (`QListWidget`)

**Sizes:**
- Default `setMaximumHeight(120)` for inline lists (e.g. patch list).
- Use full-height for side panels.

**Rules:**
1. Always `setToolTip` on the list with a one-line description.
2. Use icons via `setIconSize(QSize(16, 16))` only when items are visually distinct.
3. Selectable = single by default; use `setSelectionMode(QAbstractItemView.ExtendedSelection)` only for multi-delete workflows.

---

## 6. Status indicators

Use **color-coded roles** consistently:

| Role          | Color (light) | Color (dark) | Meaning                  |
|---------------|---------------|--------------|--------------------------|
| `status-pass` | `#16a34a`     | `#22c55e`    | CheckMesh OK, ready      |
| `status-warn` | `#f59e0b`     | `#fbbf24`    | Degraded but acceptable |
| `status-fail` | `#dc2626`     | `#ef4444`    | Mesh unusable, error     |
| `status-neutral` | `#64748b`  | `#475569`    | Idle / no result yet     |

Apply via QSS attribute selector:
```python
label.setProperty("role", "status-pass")
label.setStyleSheet("")  # let QSS handle it
```

Or programmatically with `setStyleSheet(metric_label(color, size))` from `style.py`.

---

## 7. Status bar

- **Permanent right-aligned** widgets (cell count, progress bar) — keep `font-weight: bold` when showing the real value.
- **Transient messages** (3-second auto-clear) — use `status.showMessage(text, 3000)`.
- **Indeterminate progress** (during busy state) — `_busy()` context manager handles this.

---

## 8. Log panel (`LogPanel`)

- Monospace font, 12 px (`QFont("Cascadia Code", 10)` legacy but migrated to `FONT_MONO`).
- Auto-scrolls to bottom on each new line.
- Color-coded by `Tag` prefix (`[ERROR]`, `[WARN]`, etc.).
- **Never** truncate; let the user scroll. The vertical splitter allows resizing.

---

## 9. 3D viewer (`ViewerWidget`)

- Always offer 4 background presets: White, Dark, Black, ParaView-blue.
- Persist the user's choice in `QSettings` under `viewer/background`.
- Use a fixed 8-color cycle for patches (`PATCH_COLORS`) — deterministic and colour-blind-friendly.
- Reset camera on geometry change (`plotter.reset_camera()`).

---

## 10. Dialogs

- `Dialog` parent must be the calling window (modal, blocking).
- `setMinimumWidth = 320` for any form.
- Padding = 20 px.
- Always include a `Close` button (default) and `Cancel` if changes are pending.
- Use `QMessageBox.critical` / `warning` / `information` for transient feedback.

---

## 11. Splitters

- Always vertical or horizontal — never diagonal.
- Default ratios: viewer/params 9:4, log above quality 4:1.
- Persist user-resized ratios via `QSettings` (`window/splitter_*` keys).

---

## 12. Icons

- Use Qt-native (`QStyle.standardIcon`) or QtAwesome if installed.
- 16×16 px in toolbars, 24×24 px in buttons, 32×32 px in app logo.
- Always pair icon + text on the **main action button**; icon-only is OK in toolbars.

---

## 13. Accessibility (baseline)

- All interactive widgets must have a `setToolTip` describing the action.
- Tab order should be left→right, top→bottom (default Qt behavior).
- Status info should be **both** visually shown and announced via the status bar.
- Color is never the only signal — always pair with text or icon (e.g. "PASS" text + green).

---

## 14. Theming

- **Never** call `setStyleSheet` with hardcoded colors.
- Use `style.py` helpers: `status_pill(color)`, `metric_label(color, size)`.
- For new tokens, add to `design_tokens.py` AND `design-system.json` AND the QSS file.
- Dark mode is automatic via `theme.apply_theme(app)`. No per-widget branching.
