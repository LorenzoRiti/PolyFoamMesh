# BL edge-cap root cause + robust-cap lever (Phase 4 discovery, 2026-10-05)

Exploratory probe (no prediction written before it — recorded honestly as a
discovery, not a pre-registered experiment).

## Root cause of the pancake prisms

`bl_poly._build`'s per-vertex inversion clamp is
`max_h[w] = angle_fade[w] * min(clamp_factor * d, 0.5 * min_edge[w])`,
where `min_edge[w]` is the SMALLEST edge among the vertex's incident wall
faces. Probe (`probe_bl_clamp.py`, venturi/cube_duct, BL-only + real
checkMesh):

- `clamp_factor` 0.5 / 1.0 / 2.0 → byte-identical results (the `clamp_factor*d`
  term never binds);
- `first_height` x10 → venturi stack thickness unchanged (7.05e-5 m);
- deduced: a sliver boundary face's tiny edge (venturi min_edge ≈ 1.41e-4,
  cube_duct ≈ 2.45e-4) caps the WHOLE stack at 0.5 x min_edge, and the
  consistency fixpoint floods the 1-layer pancake to the entire wall shell.

This explains why the Phase-1 base-face filter (which removes faces, not
edges) and the clamp_factor knob both failed.

## Robust-cap lever (opt-in `edge_cap_percentile`, commit 48966c1)

Replaces `0.5 * min_edge[w]` with `0.5 * percentile(incident edge lengths)`,
default 0.0 = historical min (byte-identical). Harness flag
`--poly-bl-edge-cap-pct`.

## B-side (percentile 50)

- cube_duct: BL now FAILS validation entirely ("could not insert a valid
  boundary layer"); the mesh falls back to the BL-less dual → NO 51.87 PASS,
  but **without any boundary layer** — not a promotion.
- venturi: 146.41 identical (no change).
- concave_bracket: 105.41 identical.

VERDICT: KILLED (not promoted). Releasing sliver vertices makes their own
prisms fail the face-pyramid test at every scale (the implementer measured
this on the crafted fixture), so the lever either changes nothing or drops
the BL entirely. The honest conclusion stands: the BL damage is structural
to the collapsed-dual boundary (tiny edges + thin boundary cells), and the
fix is a dual-side boundary topology change, not another clamp knob.

- [x] Discovery recorded
- [x] B-side measured
- [x] Verdict: KILLED (not promoted)
