# Buglog e note fuori-lane / decisioni trasversali

Trovare un problema fuori dalla propria lane? Annotalo qui con file+riga e
una riga di descrizione. Non sistemarlo "già che ci sei".

## [2026-09-08] Lane F — misure di igiene repo (clone size)

Clone del repo pubblico, oggetti raggiungibili (misurato via
`git rev-list --objects` + `cat-file --batch-check`):

- `origin/master` (il branch nuovo con il rename): **98.1 MB** —
  `.py` 43 MB (storia normale, ~300 commit), `.npz` 27.3 MB
  (`tests/fixtures/valve_dual.npz`, unico fixture grande tracciato),
  `.log` 12.3 MB + `.exe` 11.3 MB = **~23 MB di junk storico NON più nel
  tree** (`.opencode/opencode-loop/loop.log`, `tools/innosetup/*.exe`).
- `origin/main` (default pubblico, NON toccato dal rewrite): **37.0 MB**,
  contiene ancora `src/cfmesh_autogui/` (vecchio nome).

Decisioni:

- **`valve_dual.npz` resta tracciato.** È sotto il limite GitHub (100 MB),
  i test che lo usano sono `@pytest.mark.slow` (saltati in CI) e lo
  rigenerano via `tools/poly_fixture_builder.py --regression-fixture`.
  Passarlo a Git LFS richiederebbe `git lfs migrate` = riscrittura storia
  + force-push sul pubblico = **decisione dell'utente, non presa qui**
  (vedi regole MEGA_PLAN: mai force-push senza conferma esplicita).
- **Junk storico (~23 MB)**: i blob `.log`/`.exe` non tracciati nel tree
  gonfiano ogni clone ma rimuoverli richiede riscrittura storia +
  force-push. **Annotato per decisione utente**, non toccato.
- `.gitignore`: deduplicate le voci `dist/` / `build/` (erano 2x).
- `tools/bench_*.py`: **tutti mantenuti** (commit 2026-09-08, import
  `polyfoammesh` puliti, `bench_bl_poly_partial.py` è il gate della Lane B)
  → nessuna cancellazione.

## [2026-09-08] FUORI LANE — default branch pubblico = codice vecchio

`origin/main` (default del repo pubblico) contiene ancora
`src/cfmesh_autogui/` (pre-rename). Il codice nuovo e rinominato vive su
`master` (`origin/master`). Implicazione per la Lane G: un visitatore del
repo vede `main` come default → nome vecchio. **Decisione trasversale per
l'utente**: portare il default branch su `master` (o unire) prima/dopo la
release. Non toccato da nessuna lane per evitare conflitti.

## [2026-09-08] FUORI LANE — README.md:151 claim obsoleto

`README.md:151` afferma `dist/PolyFoamMesh.exe` ma l'artefatto reale nel
working copy è `dist/CFMesh-AutoGUI/` (build pre-rename, non tracciata).
README è il file più conteso (regole MEGA_PLAN): non toccato, annotato qui
per l'utente.

## [2026-09-09] FUORI LANE — read_label_list: binario scambiato per ASCII

Trovato durante la FASE 2 (gate salute Lane B), **risolto sul posto** perché
bloccava la verifica: `core/foam_mesh_io.py::read_label_list` usava
`b")" in probe` come prova di ASCII; un payload binario OpenFOAM con un byte
0x29 nei primi 64 byte (indice cella 41/296/10537...) passava per ASCII, il
parser per-linea restituiva una lista VUOTA e `_rename_cylinder_patches`
riscriveva il polyMesh con `neighbour 0` →dual conversion fallita a monte.
Riprodotto al 100% su un `neighbour` reale di gmshToFoam (404 KB binari →
0 elementi). Fix: ASCII solo se TUTTI i byte del probe sono
`[ \t\n\r\v\f-0-9)]`. Regression:
`tests/test_local_refinement_boxes.py::test_read_label_list_binary_payload_with_0x29_byte`.
Nota per il futuro: gmshToFoam scrive il polyMesh in BINARY quando il
controlDict del case ha `writeFormat binary` (lo skeleton del bench lo ha);
i reader gestiscono entrambi i formati — non assumere ASCII.
