# AI collaboration

This project uses artificial intelligence as a pair-programming tool, not
as a replacement for the author. I state it explicitly here because I
believe it is more useful to be transparent about the real workflow than
to leave it implied.

## How it works in practice

The workflow has two clear roles:

- **The human designs the physics and the algorithm**: the choice of the
  barycentric dual for the tet→poly conversion, the boundary-layer engine,
  the numerical tolerances, the fallback strategies when a mesh does not
  close, and the interpretation of `checkMesh` results. These are decisions
  that require understanding what physically happens to a mesh, not just
  getting the code to compile.
- **The AI writes the boilerplate under my direction**: the graphical
  interface (PySide6), unit tests, configuration templates, log parsers,
  packaging/installer scripts. Repetitive or mechanical code where the
  design choice has already been made by me, and it "only" has to be
  written well.

An honest estimate: about **60% of the lines of code** were written with
the assistance of a general-purpose AI; **100% was reviewed** by me before
being accepted — read, tested, and where necessary corrected or rewritten.

## Who did what

| Component | Main author |
|---|---|
| GUI (PySide6), panels, wizard | AI, under human direction |
| Test suite (~1,500 tests) | AI, under human direction |
| Mesh/case templates, log parsing | AI, under human direction |
| Installer/packaging scripts | AI, under human direction |
| Barycentric dual (tet→poly conversion) | Human |
| Boundary-layer engine | Human |
| Numerical tolerances and mesh-quality criteria | Human |
| Error handling, fallback and rollback | Human |

## Responsibility

The human author is fully responsible for the functioning and the
physical validity of the code. The AI was a supporting tool, not an
autonomous author: every line of generated code was reviewed, tested or
corrected by a person before entering the project. The test and
mesh-quality numbers cited in the [README](README.md) are measured, not
invented.
