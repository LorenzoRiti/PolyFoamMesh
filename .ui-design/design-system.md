# CFMesh-AutoGUI Design System

> Single source of truth for the visual language of the app.
> **Version 1.0** — July 2026

This document is the bridge between the design tokens and the running
application. For the canonical token values, see:

- `src/cfmesh_autogui/gui/design_tokens.py` — Python module (used by code)
- `.ui-design/design-system.json` — JSON master (used by tooling, audits)
- `.ui-design/qss/light.qss` and `dark.qss` — Qt stylesheets
- `.ui-design/components.md` — Per-widget usage rules

---

## At a glance

| Aspect       | Value                                                    |
|--------------|----------------------------------------------------------|
| Framework    | PySide6 (Qt 6.7+)                                        |
| Brand        | Engineer blue `#2563eb` (primary) / `#7c3aed` (secondary) |
| Color modes  | Light, Dark, System (default)                            |
| Typography   | System font stack, 11-28 px scale                        |
| Spacing      | 4 px base, Tailwind-compatible (0-64 px)                 |
| Radius       | 6 px default (subtle)                                    |
| Animation    | 120-200 ms (snappy)                                      |
| License      | MIT (open-source)                                        |

---

## Color palette

### Primary (engineer blue)
The brand color. Use for primary actions, links, focus states.

| Token         | Value      | Use                          |
|---------------|------------|------------------------------|
| `primary-50`  | `#eff6ff`  | Tinted background            |
| `primary-100` | `#dbeafe`  | Hover background, selection  |
| `primary-500` | `#3b82f6`  | Dark-mode accent             |
| `primary-600` | `#2563eb`  | **Light-mode accent (default)** |
| `primary-700` | `#1d4ed8`  | Hover (light mode)           |
| `primary-900` | `#1e3a8a`  | Text on light background     |

### Secondary (violet)
Highlight, secondary CTAs, accent within data-heavy views.

### Accent (cyan)
Data visualisation, "info" pills, graph highlights.

### Neutral (slate)
Text, borders, surfaces. **Never** use pure black `#000` or white `#fff`
for UI chrome — slate tones are easier on the eye.

### Semantic
| Token    | Light  | Dark   | Meaning            |
|----------|--------|--------|--------------------|
| success  | `#16a34a` | `#22c55e` | Pass / OK |
| warning  | `#f59e0b` | `#fbbf24` | Degraded |
| error    | `#dc2626` | `#ef4444` | Fail / broken |
| info     | `#2563eb` | `#60a5fa` | Informational |

---

## Typography

```
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
             "Helvetica Neue", Arial, sans-serif;
font-mono:   "Cascadia Code", "JetBrains Mono", "Fira Code",
             Consolas, "Courier New", monospace;
```

| Token | Size    | Use                                  |
|-------|---------|--------------------------------------|
| xs    | 11 px   | Captions, metric labels              |
| sm    | 12 px   | Helper text, log lines               |
| base  | 13 px   | Default body                         |
| md    | 14 px   | Section labels                       |
| lg    | 16 px   | Subheadings                          |
| xl    | 18 px   | Form section titles                  |
| 2xl   | 22 px   | Dialog titles                        |
| 3xl   | 28 px   | Splash screen, About dialog name     |

**Weights:** 400 (normal), 500 (medium, default for buttons), 600 (semibold, group titles), 700 (bold, status pills).

---

## Spacing scale

4 px base. Use these tokens, never hardcode px.

| Token | px  | Common use                        |
|-------|-----|-----------------------------------|
| 1     | 4   | Icon-to-text gap                   |
| 2     | 8   | Tight stack between items          |
| 3     | 12  | Default panel padding              |
| 4     | 16  | Card padding                       |
| 6     | 24  | Dialog padding                     |
| 7     | 32  | Section gap                        |
| 8     | 40  | Major section gap                  |
| 10    | 64  | Splash hero gap                    |

---

## Border radius

| Token | px   | Use                                    |
|-------|------|----------------------------------------|
| sm    | 3    | Checkbox indicator                     |
| base  | 4    | Menu items                             |
| md    | 6    | Inputs, buttons, group boxes           |
| lg    | 8    | Cards, dialogs                         |
| xl    | 12   | Large panels                           |
| pill  | 9999 | Status pills, full-pill buttons        |

---

## Component sizes

| Component     | Default  | Notes                            |
|---------------|----------|----------------------------------|
| Button (sm)   | 24 px    | Toolbar                          |
| Button (md)   | 32 px    | Form actions                     |
| Button (lg)   | 40 px    | Primary CTA in main area         |
| Input         | 32 px    | All text/number inputs           |
| Toolbar       | 36 px    | Top toolbar (viewer)             |
| Status bar    | 24 px    | Bottom status bar                |
| Progress bar  | 6 px     | Compact, slimmer than typical    |
| Menu item     | 24 px    | Comfortable touch target         |

---

## Animation

| Token  | ms  | Use                                |
|--------|-----|------------------------------------|
| fast   | 120 | Hover, focus, click                |
| normal | 200 | Toggle, expand/collapse            |
| slow   | 320 | Modal open, page transition        |
| slower | 500 | Reserved (avoid using — feels laggy) |

Easing: `cubic-bezier(0.4, 0, 0.2, 1)` (Material standard) for almost everything.

> Qt doesn't natively animate stylesheet properties, so these tokens are
> documentation. Future property animations should use `QPropertyAnimation`
> with these durations.

---

## Color modes

### Light (default for new users)
- Background: slate-50 / white
- Text: slate-900
- Accent: primary-600 `#2563eb`

### Dark
- Background: slate-950 / slate-900
- Text: slate-50
- Accent: primary-500 `#3b82f6` (brighter for contrast on dark)

### System
Follows the OS preference via `QStyleHints.colorScheme()`. Re-applied
automatically when the user changes their OS theme.

---

## How to add a new token

1. Add the value to **all three** places:
   - `src/cfmesh_autogui/gui/design_tokens.py` (Python constant)
   - `.ui-design/design-system.json` (JSON, DTCG format)
   - `.ui-design/qss/light.qss` AND `dark.qss` (QSS)
2. Add a usage example to `.ui-design/components.md`.
3. Add a test in `tests/test_design_system.py`.
4. Run the full test suite to confirm no widget regressed.

---

## How to rebrand

1. Edit `design_tokens.py` (the values the code reads).
2. Edit `design-system.json` (the canonical source).
3. Edit the QSS files (or regenerate them from a script).
4. Run `pytest tests/test_design_system.py` — fails if any hardcoded
   color leaked into widget code.
5. Bump `version` in `design-system.json` and `APP_VERSION` in
   `design_tokens.py`.

---

## What's NOT in this system

These are deliberately out of scope for v1.0:

- **Custom icons** — we use Qt's standard icons. Adding iconography would
  need an SVG sprite and a registration system.
- **Localisation (l10n)** — the app is English-only at the moment. Token
  values are not localised; UI text is wrapped in `tr()` only where
  needed.
- **High-contrast / a11y theme** — light/dark cover most users. WCAG AAA
  contrast is met for all semantic colours (verified manually).
- **Print stylesheet** — desktop app only, no print output.

---

## Changelog

- **1.0.0** (2026-07-07) — Initial design system. Light + dark + system.
  All QSS hand-written (no external lib). About dialog with vector logo.
  Component guidelines doc. 18 design-review fixes + 3 post-review bug
  fixes + 4 suggestions from the design review all live in this system.
