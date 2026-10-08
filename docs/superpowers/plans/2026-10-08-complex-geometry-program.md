# Complex-Geometry Program — Plan (commercial-grade goal)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** PolyFoamMesh meshes COMPLEX real geometries (venturi DN200, valve, mini_valve) with checkMesh PASS (max non-ortho < 70, skew < 4, no face pyramids, no negative cells) AND the full boundary layer, automatically — the same "any geometry, no fiddling" promise the commercial tools keep on simple parts.

**Architecture:** fix the DUAL quality at its source (tet_poly_dual + poly_smoother) on the representative hard case, then let the existing consuming BL (default ON) run once the dual is healthy — its auto-disable (>= 0.5% sliver base faces) is the current symptom of the dual defects, not a BL problem.

**Spec / evidence (measured in this project):**
- Venturi DN200 (`C:\cfmesh_cases\gmsh_direct_20261008_103936`, 89 surfaces): baseline (BL OFF-equivalent) REAL checkMesh FAILS: NO max 97.18, skew 65.72, aspect 51.32, 204 face pyramids, 0 negative cells. The dual itself carries the defects.
- The consuming BL (default ON since d894ee5) auto-disables there because 1.05% of wall faces are sliver-based (limit 0.5%); on the tube (0%) it builds 6 layers, checkMesh PASS.
- Simple geometries: tube/pipe/cylinder/cube_duct PASS; corpus baselines recorded for 6 geometries.
- Real checkMesh command: `wsl -d Ubuntu -- bash -lc "source /usr/lib/openfoam/openfoam2512/etc/bashrc 2>/dev/null; cd '<case>' && checkMesh"`.
- The in-process `_validate` does NOT catch checkMesh's face-pyramid/non-ortho failures: acceptance must ALWAYS be real checkMesh.

## Global Constraints

- Python: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"`.
- Keep-best: every change is measured on the venturi AND the corpus; a change that regresses any recorded baseline is reverted. No default change without the corpus.
- Deterministic output; no new dependencies.
- One lane per task; the orchestrator verifies the gate (numbers) before the next task.
- Files: this program owns `core/tet_poly_dual.py`, `core/poly_smoother.py` (if the fix is smoothing), and scratch probes. `bl_poly.py` only if a BL-side knob is needed after the dual fix (report first).

---

### Task P0: Venturi dual-defect diagnosis (measurement only)

**Files:** probe under `.superpowers/sdd/2026-10-05-poly-beat-snappy-program/` (delete after); report into `notes/complex_geometry_reasoning.md`.

- [ ] **Step 1:** Rebuild the venturi dual from `constant/polyMesh_tet_backup` with the production converter kwargs; run REAL checkMesh; capture the exact defect sets (checkMesh writes sets for face pyramids / wrong-oriented faces).
- [ ] **Step 2:** For each defect class, characterise the offending cells: position (which surface/feature), local tet density, dihedral angles, cell aspect; identify the shared geometric pattern (hypothesis candidates: concave creases between the 89 surfaces, tiny sliver tets at fillets, non-planar collapsed n-gons).
- [ ] **Step 3:** Map each pattern to the stage that creates it (input tet mesh via GMSH, barycentric dual construction, collapse, smoothing) by rebuilding with the relevant option off (collapse off, smoothing off, planar fix off) and re-running checkMesh on each variant.
- [ ] **Step 4:** Report the numbers + the ranked causes. Commit the report (no code changes).

### Task P1: Fix the dominant dual defect (keep-best)

**Files:** `core/tet_poly_dual.py` / `core/poly_smoother.py` per P0's verdict; tests.

- [ ] **Step 1:** Implement the fix indicated by P0 (candidate set, in order of expected leverage: (a) sliver-tet dissolution/merge at the offending locations; (b) targeted smoothing of the pyramid cells with the existing quality objective; (c) collapse tolerance/feature-angle tuning; (d) local re-triangulation of twisted n-gons). Gate it if it changes other geometries.
- [ ] **Step 2:** Acceptance: venturi real checkMesh improves monotonically toward PASS (report every metric); tube/pipe/cylinder/cube_duct and the recorded corpus baselines unchanged or better.
- [ ] **Step 3:** Unit tests for the fix; BL suites + fast suite green; ruff clean.
- [ ] **Step 4:** If no candidate moves the venturi without regressing others: stop, report the measured wall, return to design (do NOT ship a partial).

### Task P2: BL on the fixed venturi

- [ ] **Step 1:** With the dual healthy (sliver fraction < 0.5%), the consuming BL should auto-enable: run the venturi BL and report the layer histogram + real checkMesh.
- [ ] **Step 2:** Acceptance: 6 layers on >= 90% of wall faces, first-layer ratio 1.00, checkMesh PASS. If the sliver fraction is still above the limit, report the residual sliver faces and their geometry.

### Task P3: Generalisation + promotion

- [ ] **Step 1:** Repeat P2 on the valve and mini_valve (the other hard cases in `C:\polybench` / benchmarks corpus).
- [ ] **Step 2:** Corpus run (`run_benchmarks.py --pipeline poly`) vs baselines: no regressions.
- [ ] **Step 3:** Update the corpus baselines with the improved numbers; commit with the measured evidence; docs (CHANGELOG/GOAL/note).

### Task P4 (parallel, after P1 lands): sizing speed/quality on simple parts

- [ ] Investigate the measured adaptive-vs-manual gap (tube: adaptive 265 s with 2.6x overshoot then budget correction vs manual 28 s) and make the automatic path pick sizes closer to the manual result (fewer wasted GMSH passes). Acceptance: same final cell counts, materially lower wall time, no corpus regression.
