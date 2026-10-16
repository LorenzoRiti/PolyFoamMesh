# PolyFoamMesh Roadmap

Where the project stands after 2.3.0 and where it goes next. This
document is the planning reference: every item lists the *evidence* that
motivated it and the *acceptance gate* that closes it, in the same
measure-first style used so far (profile → fix → keep-best → corpus
zero-diff → release).

Current state (2.3.0): the poly pipeline produces polyhedral meshes with
working boundary layers on real production geometry, hangs are detected
and retried, the pipeline is 24-43 % faster with byte-identical output,
Linux is supported and verified on WSL Ubuntu, and the fast suite is
green on Windows and Linux (1,516 tests). See
[CHANGELOG.md](../CHANGELOG.md) and
[residual_risks.md](residual_risks.md) for the details and the accepted
limitations.

## Next release (2.4)

### Engineering

1. **Dual-boundary rewrite for the sharp-concave-throat class.**
   *Evidence:* the production venturi passes every topology check but
   still reports `Failed 3` at the throat (NO 97, skew 44, a few hundred
   inverted face pyramids); measured to be topological, not a resolution
   problem (an 8.6× finer input reproduces it exactly), and unreachable
   by merge/normal-repair (P5 spike). *Gate:* the throat class passes
   checkMesh (NO < 70, no inverted faces) on the venturi and valve
   without regressing the corpus; bit-identical output elsewhere.

2. **Boundary layers on multi-million-cell meshes.**
   *Evidence:* the BL is skipped above the 8M-face guard (anti-crash
   decision measured on a 5M-cell run). *Direction:* chunked precompute
   and build with a measured memory ceiling. *Gate:* Parte4-class medium
   mesh gets a full BL within a measured RAM budget; the guard stays for
   meshes above the new ceiling.

3. **Adaptive sizing: quality of the retry path.**
   *Evidence:* the signature-retry enables the 2D optimizer only there
   (2-3× cost on large parts); field hygiene could prevent some
   non-convergence upstream. *Gate:* corpus and the runaway geometry
   keep their measured results; any field clamp proven keep-best.

4. **Performance follow-ups (opportunistic).**
   Global-windings BFS ~1.2× (measured safe gain), the polyMesh writer
   on very large meshes, GMSH threading if a measured win appears.
   *Gate:* byte-identical outputs or keep-best with checkMesh parity.

### Product and platform

5. **Linux hardening.** Desktop-GPU verification, Fedora and Arch
   (issue #11 class), an AppImage evaluation (currently deferred and
   documented as such). *Gate:* the verified-vs-not box in
   INSTALL_LINUX.md shrinks honestly.

6. **Benchmark parity.** Run the corpus through the same fallback chain
   the app uses (standard → concave closure → halved first layer) and
   record hex-pipeline baselines, so harness numbers match product
   behaviour.

7. **UX backlog.** The SAMR/solution-adaptive ribbon entries (currently
   disabled as “coming in a future release”), case templates, the BC
   editor flow. Each ships behind the same gates as the meshing engine.

### Release process

Tags drive the release: pushing `v*` runs the workflow that gates
(ruff, version sync, fast suite), builds the Windows installer and
portable zip, builds the Linux tarball, and publishes the GitHub release
with notes from the CHANGELOG. Before tagging: corpus zero-diff, both
platforms green, smoke test on the two reference cases (venturi and the
refined tube), README screenshots current.

## Deferred (next cycle, not forgotten)

- Dual-boundary rewrite is the single biggest quality lever and the
  first item above for that reason.
- `_solve_global_windings` ~1.2× (safe but small).
- Hex (cfMesh) corpus baseline refresh after the parity work in item 6.
