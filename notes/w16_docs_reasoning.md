# W16 — reasoning and predictions

Date: 2026-10-23. Predecessor: `notes/w16_docs_diagnosis.md`.
Predictions written BEFORE any edit/build/test.

## B1 — v2 append-only extension

Prediction: adding two appendices (G: fTetWild experimental path with
measured numbers; H: engineering-status update W10.2→WALL-03) before
`# === APPEND HERE ===` and rebuilding grows the PDF (pages+,
TOC gains exactly G and H) with zero changes to existing sections
(verified by extracting text before/after and diffing modulo the
appended tail). Falsification: build fails, page count shrinks, an
existing section's text changes, or TOC misses an appendix → stop,
isolate, no commit.

## B2 — v1 deprecation guard

Prediction: a banner + hard guard (refuse to run without explicit
`POLYFOAMMESH_ALLOW_LEGACY_REPORT=1`) breaks nothing in-repo
(nothing imports/calls v1 — verified by grep) while making the
PDF-clobbering accident impossible. Falsification: any in-repo or
documented workflow calls v1 → downgrade to warning-only and
document the caller instead.

## B3 — numbers tests

Prediction: all assertions hold on first run because every figure
was copied from the cited artifact, with tolerances where floats are
involved (JSON floats, LEADERBOARD rounding). Fragile-test risk is
structural: none of these tests run a mesher or read the clock.
Falsification: any mismatch means EITHER the doc invented a number
(fix the doc, cite the artifact) OR the artifact moved (fix the
test's path, never the number). No silent tolerance widening: a
widened tolerance must name the new evidence in the test comment.

## B4 — USER_GUIDE/README touch-ups

Prediction: small diffs only (README selector text + caption +
screenshots-staleness flag; USER_GUIDE BL-on-tets already present —
verify, add at most the dirty-cad fTetWild line and the WALL-03
mini_valve dual-only line where those sections live). Falsification:
discovering a stale command or number not in the diagnosis → fix it
in the same spirit, extend the numbers test if it is a number.

## B5 — gate

Full fast suite + ruff must stay green; the PDF rebuild is the only
slow step (~minutes). Commit once, all W16 files together (notes,
tests, generator, guard, docs, GOAL/CHANGELOG entry).
