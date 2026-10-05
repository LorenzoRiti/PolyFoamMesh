# PolyFoamMesh vs snappyHexMesh — Leaderboard

Runners: hex, poly, gmsh_direct, snappy_base, snappy_expert

## cube_duct

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 10496 | 18258 | 18791 | 87981 | 150836 |
| non-ortho max | 64.86 | 80.64 | 71.67 | 46.21 | 64.27 |
| skew max | 0.59 | 4.92 | 1.11 | 0.49 | 0.79 |
| aspect max | 4.39 | 554.2 | 8.72 | 12.96 | 25.74 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 3.12 | 8.34 | 2.21 | 7.23 | 12.25 |
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
| time (s) | 2.75 | 3.89 | 2.1 | 4.0 | 7.63 |
| thresholds | PASS | PASS | PASS | PASS | PASS |

**Winner:** hex

## groove1

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 9768 | 1002 | 4012 | 152130 | 265760 |
| non-ortho max | 75.98 | 50.59 | 61.07 | 47.6 | 42.71 |
| skew max | 2.09 | 3.67 | 1.05 | 1.83 | 0.65 |
| aspect max | 8.03 | 5.63 | 5.8 | 16.82 | 28.13 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 6.92 | 3.03 | 1.55 | 12.17 | 22.19 |
| thresholds | FAIL | PASS | PASS | PASS | PASS |

**Winner:** snappy_expert

## slot1

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 34090 | 8681 | 38036 | 107776 | 127888 |
| non-ortho max | 78.8 | 56.01 | 65.58 | 42.42 | 39.4 |
| skew max | 2.11 | 3.59 | 0.81 | 3.05 | 0.5 |
| aspect max | 10.02 | 5.71 | 6.87 | 14.44 | 17.55 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 9.56 | 12.92 | 2.44 | 8.73 | 9.48 |
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
| time (s) | 29.82 | 64.2 | 3.83 | 605.76 | 1201.29 |
| thresholds | FAIL | PASS | PASS | PASS | FAIL |

**Winner:** snappy_base

## venturi

| metric | hex | poly | gmsh_direct | snappy_base | snappy_expert |
|---|---|---|---|---|---|
| cells | 18400 | 4732 | 10136 | 22824 | 26660 |
| non-ortho max | 53.37 | 146.41 | 73.91 | 63.04 | 60.26 |
| skew max | 2.29 | 3.66 | 3.19 | 0.68 | 0.73 |
| aspect max | 5.53 | 108.74 | 11.65 | 10.1 | 14.75 |
| neg cells | 0 | 0 | 0 | 0 | 0 |
| time (s) | 3.16 | 6.61 | 4.63 | 6.37 | 6.09 |
| thresholds | PASS | FAIL | FAIL | PASS | PASS |

**Winner:** hex

## Aggregates

### Pass rate (terrain B — reliability)

| runner | pass rate |
|---|---|
| hex | 50% |
| poly | 67% |
| gmsh_direct | 67% |
| snappy_base | 100% |
| snappy_expert | 83% |

### Median metrics over passing runs (terrain A — quality)

| runner | non-ortho | skew | aspect | time (s) |
|---|---|---|---|---|
| hex | 53.37 | 0.7 | 5.53 | 3.12 |
| poly | 61.13 | 3.33 | 8.12 | 8.4 |
| gmsh_direct | 63.33 | 0.86 | 6.52 | 2.27 |
| snappy_base | 46.91 | 0.59 | 13.7 | 7.98 |
| snappy_expert | 60.26 | 0.67 | 17.55 | 9.48 |

### Exclusive passes (terrain C — robustness)

- **hex**: (none)
- **poly**: (none)
- **gmsh_direct**: (none)
- **snappy_base**: (none)
- **snappy_expert**: (none)
