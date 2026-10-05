# PolyFoamMesh vs snappyHexMesh — Leaderboard

Runners: hex, poly, gmsh_direct, snappy_base, snappy_expert

## concave_bracket

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 51631 | 35097 | 113623 | 316623 | 446090 |
| non-ortho max | 67.3 | 105.41 | 67.5 | 71.75 | 64.76 |
| skew max | 2.17 | 4.37 | 1.04 | 5.59 | 2.36 |
| aspect max | 9.46 | 566.63 | 7.84 | 20.25 | 25.23 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 7.35 | 29.09 | 4.17 | 88.31 | 149.19 |
| thresholds | PASS | FAIL | PASS | FAIL | PASS |

**Winner:** snappy_expert

## cube_duct

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 10496 | 18258 | 18791 | 87981 | 150836 |
| non-ortho max | 64.86 | 80.64 | 71.67 | 46.21 | 64.27 |
| skew max | 0.59 | 4.92 | 1.11 | 0.49 | 0.79 |
| aspect max | 4.39 | 554.2 | 8.72 | 12.96 | 25.74 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 2.95 | 8.21 | 2.22 | 7.26 | 12.24 |
| thresholds | PASS | FAIL | FAIL | PASS | PASS |

**Winner:** snappy_base

## cylinder

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 4166 | 8969 | 8927 | 28048 | 36778 |
| non-ortho max | 52.83 | 66.72 | 60.61 | 52.96 | 63.1 |
| skew max | 0.7 | 2.77 | 0.71 | 0.45 | 0.67 |
| aspect max | 6.17 | 97.23 | 6.17 | 10.77 | 17.16 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 2.61 | 3.86 | 2.12 | 3.9 | 7.42 |
| thresholds | PASS | PASS | PASS | PASS | PASS |

**Winner:** hex

## dirty_cad

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 0 | 0 | 0 | 0 | 0 |
| non-ortho max | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| skew max | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| aspect max | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 0.0 | 0.0 | 0.0 | 0.88 | 1.31 |
| thresholds | FAIL | FAIL | FAIL | FAIL | FAIL |

**Winner:** none (no runner passed thresholds)

## groove1

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 9768 | 1002 | 4012 | 152130 | 265760 |
| non-ortho max | 75.98 | 50.59 | 61.07 | 47.6 | 42.71 |
| skew max | 2.09 | 3.67 | 1.05 | 1.83 | 0.65 |
| aspect max | 8.03 | 5.63 | 5.8 | 16.82 | 28.13 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 6.74 | 2.91 | 1.54 | 12.05 | 22.5 |
| thresholds | FAIL | PASS | PASS | PASS | PASS |

**Winner:** snappy_expert

## mini_valve

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 611958 | 0 | 0 | 0 | 0 |
| non-ortho max | 72.58 | 0.0 | 0.0 | 0.0 | 0.0 |
| skew max | 2.4 | 0.0 | 0.0 | 0.0 | 0.0 |
| aspect max | 10.92 | 0.0 | 0.0 | 0.0 | 0.0 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 41.29 | 84.56 | 84.56 | 1204.07 | 1200.91 |
| thresholds | FAIL | FAIL | FAIL | FAIL | FAIL |

**Winner:** none (no runner passed thresholds)

## multi_inlet

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 7680 | 0 | 0 | 41563 | 57136 |
| non-ortho max | 49.96 | 0.0 | 0.0 | 64.3 | 64.77 |
| skew max | 0.7 | 0.0 | 0.0 | 7.86 | 7.72 |
| aspect max | 6.3 | 0.0 | 0.0 | 34.32 | 40.66 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 2.79 | 4.02 | 4.01 | 8.1 | 19.26 |
| thresholds | PASS | FAIL | FAIL | FAIL | FAIL |

**Winner:** hex

## slot1

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 34152 | 8681 | 38036 | 107776 | 127888 |
| non-ortho max | 77.28 | 56.01 | 65.58 | 42.42 | 39.4 |
| skew max | 2.11 | 3.59 | 0.81 | 3.05 | 0.5 |
| aspect max | 10.03 | 5.71 | 6.87 | 14.44 | 17.55 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 11.06 | 12.27 | 2.41 | 8.7 | 9.63 |
| thresholds | FAIL | PASS | PASS | PASS | PASS |

**Winner:** snappy_expert

## thin_gap

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 666976 | 27205 | 109487 | 7693005 | 0 |
| non-ortho max | 73.75 | 66.25 | 65.84 | 42.28 | 0.0 |
| skew max | 2.16 | 3.07 | 0.91 | 0.5 | 0.0 |
| aspect max | 9.15 | 10.54 | 7.3 | 14.67 | 0.0 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 29.41 | 58.25 | 3.74 | 567.52 | 1201.28 |
| thresholds | FAIL | PASS | PASS | PASS | FAIL |

**Winner:** snappy_base

## venturi

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 18400 | 4732 | 10136 | 22824 | 26660 |
| non-ortho max | 53.33 | 146.41 | 73.91 | 63.04 | 60.26 |
| skew max | 2.29 | 3.66 | 3.19 | 0.68 | 0.73 |
| aspect max | 5.53 | 108.74 | 11.65 | 10.1 | 14.75 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 3.04 | 6.65 | 4.62 | 6.38 | 5.99 |
| thresholds | PASS | FAIL | FAIL | PASS | PASS |

**Winner:** hex

## Aggregates

### Pass rate (terrain B — reliability)

| runner | pass rate |
|---|---|
| hex | 50% |
| poly | 40% |
| gmsh_direct | 50% |
| snappy_base | 60% |
| snappy_expert | 60% |

### Median metrics over passing runs (terrain A — quality)

| runner | non-ortho | skew | aspect | time (s) |
|---|---|---|---|---|
| hex | 53.33 | 0.7 | 6.17 | 2.95 |
| poly | 61.13 | 3.33 | 8.12 | 8.06 |
| gmsh_direct | 65.58 | 0.91 | 6.87 | 2.41 |
| snappy_base | 46.91 | 0.59 | 13.7 | 7.98 |
| snappy_expert | 61.68 | 0.7 | 21.39 | 10.94 |

### Exclusive passes (terrain C — robustness)

- **hex**: multi_inlet
- **poly**: (none)
- **gmsh_direct**: (none)
- **snappy_base**: (none)
- **snappy_expert**: (none)
