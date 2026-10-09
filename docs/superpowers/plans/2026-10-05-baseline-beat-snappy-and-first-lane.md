# Baseline Beat-Snappy and First Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a valid snappyHexMesh comparison baseline (fast + full tiers), write the severity-ordered gap analysis, and kick off the first improvement lane with a prediction + A-side measurement — on a clean tree.

**Architecture:** Benchmark-first as approved in the spec. Tasks 1-8 of the companion plan (harness: corpus, generators, gates, snappy base/expert builders, comparison runner, leaderboard) are DONE and committed; this plan executes Tasks 9-12 plus the triage of uncommitted work. No mesher algorithm is touched in this plan: the fix is a follow-up plan chosen by measured gaps.

**Tech Stack:** Python 3.11 (project interpreter), pytest, ruff, WSL2 Ubuntu + OpenFOAM v2512, harness under `benchmarks/`.

**Spec:** `docs/superpowers/specs/2026-09-22-beat-snappy-benchmark-design.md`
**Companion plan (Tasks 1-8, done):** `docs/superpowers/plans/2026-09-22-beat-snappy-benchmark.md`

## Global Constraints

- Interpreter: use `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"` (the bare `python` on PATH is an unrelated venv without project deps and fails collection with 48 errors).
- Run all commands from repo root `C:\Users\Davide Valoroso\cfmesh-autogui`.
- `ruff check src/ tests/` must be green; `pytest -m "not slow and not wsl"` must be green (baseline: 1232 passed / 2 skipped / 1 xfailed).
- Never change mesher defaults without an opt-in flag and a measured A/B (repo rule).
- No push. Local commits only (spec decision D8). `master` is 55 commits ahead of `origin/main`, diverged — publishing is out of scope.
- Benchmark hard cap `RUN_TIMEOUT_S = 1500`; never raise it. A `timed_out` record is terrain-B data, not a harness bug.
- A meshing failure (record with `thresholds_pass=False` and real mesher `errors`) is VALID data — do not "fix" it. A harness bug (traceback in parent log, missing record, `child exited 2`) must be fixed in the owning module with a regression test, then re-run.
- WSL Ubuntu exists but starts stopped; the harness handles OpenFOAM sourcing (proven by existing `benchmarks/results/comparison_*.json`).

---

### Task 1: Hygiene and triage of uncommitted work

**Files:**
- Stash: `src/polyfoammesh/core/bl_poly.py` (experimental `transition_zone`, never validated)
- Revert: `src/polyfoammesh/core/native_mesher.py` (unreachable refinement dead code)
- Delete: `test_new_features.py` (scratch, hardcoded `C:/polybench` paths)
- Commit: `docs/superpowers/specs/2026-09-22-beat-snappy-benchmark-design.md`, `docs/superpowers/plans/2026-09-22-beat-snappy-benchmark.md`, `docs/superpowers/plans/2026-10-05-baseline-beat-snappy-e-prima-lane.md`

**Interfaces:**
- Consumes: nothing
- Produces: clean git tree (stash entry kept), roadmap docs committed, verified green baseline for later tasks

- [ ] **Step 1: Stash the experimental BL change**

```powershell
git stash push -m "bl: transition_zone experimental (non validato)" -- src/polyfoammesh/core/bl_poly.py
```

Expected: stash created; `git stash list` shows the entry.

- [ ] **Step 2: Revert the dead native_mesher change**

```powershell
git checkout -- src/polyfoammesh/core/native_mesher.py
```

Expected: `git diff --stat` no longer lists `native_mesher.py`.

- [ ] **Step 3: Delete the scratch script**

```powershell
Remove-Item -LiteralPath "test_new_features.py"
```

Expected: file gone.

- [ ] **Step 4: Commit the roadmap documents**

```powershell
git add docs/superpowers/specs/2026-09-22-beat-snappy-benchmark-design.md docs/superpowers/plans/2026-09-22-beat-snappy-benchmark.md docs/superpowers/plans/2026-10-05-baseline-beat-snappy-e-prima-lane.md
git commit -m "docs: beat-snappy spec e piano baseline (locale)"
```

- [ ] **Step 5: Verify the tree and the baseline**

```powershell
git status --short
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m ruff check src/ tests/
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/ -q -m "not slow and not wsl" -p no:cacheprovider --tb=line
```

Expected: `git status --short` shows no modified/untracked tracked-area files (`.superpowers/` is self-ignored); ruff "All checks passed!"; pytest ≥1232 passed / 2 skipped / 1 xfailed / 0 failed.

- [ ] **Step 6: Confirm no further changes to commit**

```powershell
git status --short
```

Expected: empty. If any tracked file changed, commit it with a clear message.

**Acceptance:** tree clean; stash entry present; docs committed; ruff + fast suite green.

---

### Task 2: Valid fast-tier baseline

**Files:**
- Produce: `benchmarks/results/comparison_<ts>.json` (fast tier), `benchmarks/results/LEADERBOARD.md`
- Modify only if a harness bug is found: owning module under `benchmarks/` + regression test under `tests/`

**Interfaces:**
- Consumes: Task 1 clean tree
- Produces: valid fast-tier comparison JSON + leaderboard; evidence every runner works after the OF v2512 compat fix (commit `860cf43`)

- [ ] **Step 1: Run the fast tier**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier fast
```

Use a long command timeout (at least 3 600 000 ms). Expected: 6 geometries × 5 runners = 30 records; log lines `RUN <geom> / <runner>`; exit code 0 or 1 (gates are the point).

- [ ] **Step 2: Verify the record file**

```powershell
$r = Get-ChildItem benchmarks/results/comparison_*.json | Sort-Object LastWriteTime | Select-Object -Last 1
$j = Get-Content $r.FullName | ConvertFrom-Json
"records: $($j.Count)"
$j | Group-Object runner | ForEach-Object { "$($_.Name): $($_.Count)" }
$j | Where-Object { -not $_.thresholds_pass } | Format-Table geometry, runner, failed_thresholds, errors -Wrap
```

Expected: 30 records, 6 per runner; every one of the 5 runners has at least one record with `cell_count > 0` (snappy included — if a snappy run still has 0 cells and an empty `errors`, that is a harness bug → Step 4); failures have non-empty `failed_thresholds` or `errors`.

- [ ] **Step 3: Build the leaderboard**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/compare_snappy.py --results <file from Step 2>
```

Expected: `benchmarks/results/LEADERBOARD.md` created containing all 6 fast geometries.

- [ ] **Step 4: Fix-and-loop ONLY for harness bugs**

A harness bug is: traceback in the parent log, a missing record, `child exited 2`, or a runner producing 0 cells with empty `errors`. Fix in the owning `benchmarks/` module with a regression test under `tests/`, then re-run Step 1. A meshing failure is valid data — do not fix.

- [ ] **Step 5: Commit**

```powershell
git add benchmarks/results/comparison_*.json benchmarks/results/LEADERBOARD.md
git commit -m "bench: baseline fast tier vs snappy (locale)"
```

If Step 4 changed code/tests, include those paths in the same or a separate commit.

**Acceptance:** 30/30 records, 5 working runners, `LEADERBOARD.md` on disk, committed.

---

### Task 3: Full baseline + official leaderboard

**Files:**
- Produce: `benchmarks/results/comparison_<ts>.json` (full tier), official `benchmarks/results/LEADERBOARD.md`
- Modify only if a geometry blows the budget: `benchmarks/generate_corpus.py` (scale down) or `benchmarks/corpus.py` (tier change) with a note in the record `warnings`; never raise `RUN_TIMEOUT_S`

**Interfaces:**
- Consumes: Task 2 stable fast tier
- Produces: the baseline every future lane compares against

- [ ] **Step 1: Run the full tier**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full
```

Use the longest available command timeout (8 hours = 28 800 000 ms if needed). Expected: 10 geometries × 5 runners = 50 records. Realistically far less than the worst case; historical valve runs excluded by corpus design.

- [ ] **Step 2: Build the official leaderboard**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/compare_snappy.py --results <full json> --out benchmarks/results/LEADERBOARD.md
```

- [ ] **Step 3: Enforce the 25-min budget per record**

```powershell
$j = Get-Content <full json> | ConvertFrom-Json
$j | Where-Object { $_.wall_time_s -gt 1500 } | Format-Table geometry, runner, wall_time_s
```

Expected: no rows. `timed_out=True` rows are terrain-B data for Task 4, not a reason to raise the cap.

- [ ] **Step 4: Commit the baseline artefacts**

```powershell
git add benchmarks/results/comparison_*.json benchmarks/results/LEADERBOARD.md
git commit -m "bench: baseline full-tier leaderboard vs snappy (locale)"
```

**Acceptance:** 50 records (or documented exclusions in `warnings`), no budget violation, official `LEADERBOARD.md`, committed.

---

### Task 4: Gap analysis document

**Files:**
- Create: `benchmarks/results/GAP_ANALYSIS.md`

**Interfaces:**
- Consumes: Task 3 `LEADERBOARD.md` + comparison JSON
- Produces: severity-ordered gap list that picks the first lane (Task 5)

- [ ] **Step 1: Extract gaps from the leaderboard and JSON**

A gap is any cell where:
1. one of OUR runners has `thresholds_pass=False` (absolute-threshold miss), OR
2. one of OUR runners has `thresholds_pass=True` but both snappy runners beat it on `max_non_ortho` or `max_skewness` (relative loss), OR
3. a runner `timed_out` (reliability miss).

- [ ] **Step 2: Write the analysis**

```markdown
# Gap analysis — baseline vs snappy (YYYY-MM-DD)

Source: benchmarks/results/LEADERBOARD.md + comparison_<ts>.json

## Executive summary
<3-5 sentences: overall pass rates, who wins most cells, the single most
important problem.>

## Gap table (severity order)

| # | Geometry | Our runner | Gap type | Failing/worse metric | Us | Best snappy | Notes |
|---|----------|-----------|----------|----------------------|----|-------------|-------|
| 1 | ...      | poly      | threshold miss | neg_cells | 3 | 0 | ... |

Severity order (spec): neg cells > non-ortho > skew > aspect > time.
Rows sorted by that order; ties broken by how many geometries share the gap.

## Terrain C observations
<geometries where snappy failed and we passed, and vice versa>

## Chosen first lane
<name it after the top gap cluster, e.g. "Lane BL: prism collapse on
concave/mini_valve"> — justification = it holds the most/severest rows.
```

Every table row carries real numbers from the JSON. No speculation: ambiguous cells say so in Notes.

- [ ] **Step 3: Commit**

```powershell
git add benchmarks/results/GAP_ANALYSIS.md
git commit -m "bench: gap analysis of baseline vs snappy"
```

**Acceptance:** every row has real numbers; the first lane is named and justified by data.

---

### Task 5: Kick off the first lane (prediction + A-side measurement) — then STOP

**Files:**
- Create: `notes/<lane-slug>_reasoning.md` (slug = kebab of the lane name from Task 4)
- Possibly create: measurement script under `tools/` if the gap needs a focused A/B harness
- Append: `.opencode/GOAL.md` progress entry for 2026-10-05

**Interfaces:**
- Consumes: Task 4 "Chosen first lane"
- Produces: prediction written BEFORE any fix + recorded A-side measurement; then STOP and report to the user. The fix itself is a FOLLOW-UP plan.

- [ ] **Step 1: Write the prediction note BEFORE touching any mesher code**

```markdown
# <Lane name> — reasoning (YYYY-MM-DD)

## Gap being addressed
(from GAP_ANALYSIS.md: rows #1..#k, numbers verbatim)

## Hypothesis
<what in the code causes it — file:function, mechanism, ≤10 lines>

## Prediction (write BEFORE the experiment)
- On <geometry>: <metric> will move from <current> to <predicted range>
- No change expected on healthy geometries (cylinder/cube_duct): <identical metrics>
- Cost: <expected wall-time delta>

## Measurement plan
- A-side (current code): command + JSON record ref  ← measured in Step 2
- B-side (with fix): same command, fix behind flag <flag name or "none, additive">
- Kill criterion: if B does not improve the primary metric on the gap
  geometry, or regresses any fast-tier geometry, the lane is killed
  ("measured no-op, not promoted" — repo precedent).

## Status
- [ ] Prediction written
- [ ] A-side measured
- [ ] Fix implemented (follow-up plan)
- [ ] B-side measured
- [ ] Verdict
```

- [ ] **Step 2: Record the A-side measurement**

Re-run the specific geometry×runner from the gap (fast tier if possible):

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full --geometry <geom> --runner <runner>
```

Paste the record (thresholds, metrics) into the note under "A-side".

- [ ] **Step 3: Append the GOAL.md progress entry**

Add an `## Avanzamento 2026-10-05` section to `.opencode/GOAL.md` summarizing: fast + full baseline pass rates, top 3 gaps, chosen lane, prediction. Keep the repo's existing Italian style.

- [ ] **Step 4: Commit and report**

```powershell
git add notes/<lane-slug>_reasoning.md .opencode/GOAL.md
git commit -m "notes: <lane> prediction + A-side baseline (locale)"
```

Then STOP and report to the user: baseline pass rates, top 3 gaps, chosen lane, prediction — and ask whether to proceed with the follow-up fix plan.

**Acceptance:** prediction committed before any mesher code change; A-side numbers recorded; GOAL.md updated; report delivered.

---

## Self-Review

**Spec coverage:**
- Task 9 fast-tier smoke → Task 2 ✓
- Task 10 full baseline + LEADERBOARD → Task 3 ✓
- Task 11 gap analysis → Task 4 ✓
- Task 12 lane kickoff (prediction + A-side) → Task 5 ✓
- Triage of uncommitted work (pre-requisite for clean measurements) → Task 1 ✓
- No push, no default changes, hard cap untouched → Global Constraints ✓

**Placeholder scan:** the only `<...>` tokens are runtime data fill-ins in Task 4/5 templates (executor has the artifacts open); no TBD, no "similar to Task N".

**Type consistency:** runner names (`hex`, `poly`, `gmsh_direct`, `snappy_base`, `snappy_expert`) and CLI flags (`--tier`, `--geometry`, `--runner`, `--results`, `--out`) match the committed harness (`benchmarks/run_comparison.py`, `benchmarks/compare_snappy.py`); record fields match `make_record` (`thresholds_pass`, `failed_thresholds`, `wall_time_s`, `timed_out`, `cell_count`, `errors`, `warnings`).
