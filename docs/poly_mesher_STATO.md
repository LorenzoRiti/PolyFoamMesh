# Stato del mesher poliedrico (CFMesh-AutoGUI 2.1.0)

Documento tecnico — cosa c'è, come funziona, cosa è stato verificato,
limiti e prossimi passi. Aggiornato al 2026-08-07.

## 1. Obiettivo (dal paper di riferimento)

L'utente vuole la topologia **STAR-CCM+/ANSYS Fluent**:
[Sosnowski, Krzywanski, Gnatowska, E3S Web Conf. 14, 01027 (2017)](https://www.e3s-conferences.org/articles/e3sconf/pdf/2017/02/e3sconf_ef2017_01027.pdf)
definisce il mesh poliedrico come la **conversione TET → POLY**: nuove
facce tra i centroidi degli spigoli e i centri delle facce, collegate al
centro cella (il **barycentric dual**), con merge delle facce adiacenti.

**Percorso corretto: tet (GMSH) → dual → 100% poly irregolare.**
Il dual mediano di una griglia **cartesiana** è autoduale (cubi al
centro) e NON dà la topologia voluta — il percorso "Native Poly"
(cartesiano) è stato rimosso dalla GUI per questo motivo.

## 2. Percorso attivo: CFD Poly (GMSH, no-WSL)

```
STL/STEP → GMSH volume (tet) → checkMesh (WSL, gate) → TetPolyDualConverter
         → constant/polyMesh 100% poly (3-10 facce/cella) → checkMesh finale
```

- Generazione: **GMSH + il nostro dual** (`core/tet_poly_dual.py`,
  824k tet → dual in ~33 s, pure numpy).
- WSL serve **solo** per la validazione checkMesh (il gate "prima
  checkMesh PASS, poi dual"), mai per generare.

### Verifica end-to-end (venturi.stl, coarse)
| Step | Risultato |
|---|---|
| GMSH tet | 39.792 celle, 1 s |
| checkMesh (tet) | **Mesh OK** (skew 2.89, non-ortho 54.6°, pyramids OK) |
| dual | 8.074 celle 100% poly (4-10 facce, nessun hex) |
| checkMesh (poly) | **Mesh OK** |

## 3. Fix applicati oggi (commits 0234bae → a829131 → 16e52e1 → 80d9bbf → 44b9613/8a9312e)

| Fix | File | Effetto |
|---|---|---|
| `forReparametrization=True` | `core/gmsh_wrapper.py` | sblocca "Invalid exterior boundary mesh for parametrization" su STL discreti chiusi |
| `n_int = count(neighbour >= 0)` | `core/tet_poly_dual.py` | dual su tet con neighbour padded (msh_to_of_polymesh) |
| pulizia triangoli coplanari contenuti | `core/native_mesher.py`, `core/stl_writer.py` (export GMSH) | STL con triangoli sovrapposti (es. condotto con foro mal tagliato) non rompono più GMSH/HXT |
| rimozione "Native Poly" dalla GUI | gui/params_panel, main_window, mesh_engine | il dual cartesiano confondeva; resta il percorso giusto |
| spec one-dir + DLL gmsh + `_cli_main` | CFMesh-AutoGUI.spec, app.py, gmsh_wrapper | frozen exe: GMSH funziona (verificato: 46.496 elementi) |

## 4. Moduli rilevanti

- `core/tet_poly_dual.py` — dual TET→POLY (il motore del percorso CFD Poly).
- `core/hex_poly_dual.py` — dual mediano generico (hex/poly, usato dal
  percorso nativo rimosso; resta per test/bench).
- `core/native_mesher.py` — cut-cell castellated + scanline classification
  (modulo del percorso nativo; usato dai test, nessun riferimento GUI).
- `core/gmsh_wrapper.py` / `gmsh_subprocess.py` — generazione tet GMSH.
- `core/mesh_converter.py` — `.msh` → OpenFOAM polyMesh.
- `commercial/mesh_engine.py` — selezione algoritmo (9 algoritmi, niente
  NativePoly).
- `commercial/native_poly_bridge.py` — bridge del percorso nativo
  (solo test/bench).

## 5. Limiti noti (misurati, onesti)

1. **STL auto-intersecanti** (triangoli che si incrociano, foro non
   tagliato nel CAD): GMSH fallisce con "PLC Error: two segments
   intersect". La pulizia coplanare rimuove i contenuti ma non ricrea i
   fori — serve rieksportare lo STL dal CAD.
2. **Il dual dei tet richiede celle pulite**: GMSH le produce (checkMesh
   PASS), quindi ok.
3. **Qualità del mesh nativo cartesiano-dual** (rimosso): skew 7.58,
   non-ortho 44.6 — non è la topologia richiesta.
4. **Percorsi WSL** (cfMesh, checkMesh): richiedono WSL2+OpenFOAM; il
   pacchetto demo non lo include.

## 6. Prossimi passi suggeriti

1. ~~**Snapping/quality phase** per il percorso nativo~~ — **fatto**
   (commit `67fa285`, 2026-08-07): `hex_poly_dual.py` ora chiama
   `poly_smoother.smooth_dual_mesh` con lo stesso contratto keep-best già
   usato da `tet_poly_dual.py` (accettato solo se i difetti totali non
   peggiorano e nessun volume cella diventa non-positivo). Misurato su
   sfera icosaedrica via `native_mesher` → `hex_poly_dual`: subdiv=2/
   cell=0.15, difetti totali 33 → 6; subdiv=3/cell=0.12, 249 → 231.
   Non chiude lo skew a zero da solo — resta valido il punto 6.1 sotto.
2. **Test su macchina pulita** dell'installer (nessuna VM disponibile in
   ambiente; verificato per self-containment + funzionamento dal
   percorso installato).
3. Firma Authenticode per eliminare l'avviso SmartScreen.

### 6.1 Verso un mesher stile scFLOW (voxel/cut-cell → poly automatico)

Confronto con scFLOW (Cradle CFD) del 2026-08-07: stesso principio
architetturale (griglia cartesiana → cut-cell → poly), stesso percorso
del nostro `native_mesher.py` + `hex_poly_dual.py`. Gap concreti, con
scoping tecnico onesto (perché NON sono stati implementati in questa
sessione — richiedono riscritture invasive del core geometrico, rischiose
senza una campagna di test dedicata):

- **Refinement locale (octree o griglia graduata)** — oggi
  `native_mesher._make_grid`/`_build` usano `cell` come UNO scalare
  globale: ogni coordinata di corner è `grid.lo + idx * cell`
  (`_build`, righe ~434-436, ~515-516 e a cascata in tutta la
  classificazione/clipping). Generalizzare a spaziatura non uniforme
  per asse (griglia graduata, senza hanging node) tocca ~450 righe in
  `_build` — fattibile e più sicuro di un octree vero (che introduce
  T-junction/hanging node e richiede split delle facce lato grossolano),
  ma richiede una riscrittura dedicata + regressione completa su
  `tests/test_native_mesher.py` prima di essere accettata. **Non
  tentata qui**: il rischio di corrompere silenziosamente la topologia
  (mesh non watertight non rilevata da un test parziale) è la ragione
  per cui il codice esistente segue sempre il pattern "keep-best,
  verificato contro il detector in-process" — un rewrite del genere
  merita lo stesso trattamento, non una patch rapida.
- **Boundary layer sul percorso nativo** — `commercial/bl_engine.py`
  esiste ma è un CALCOLATORE di parametri (y+, altezza primo layer),
  non un inseritore di geometria: produce numeri che oggi alimentano
  il `meshDict` di cfMesh o l'estrusione GMSH. Non esiste in repo alcun
  algoritmo di estrusione prismatica che parta da un poly mesh nativo
  già costruito. Questo è lavoro algoritmico nuovo (estrusione facce di
  parete + blend con l'interno cut-cell), non un collegamento di pezzi
  esistenti — stimato più grande del punto precedente.
- **Refinement per parte/regione** — dipende dal punto 1 (serve prima
  una griglia non uniforme prima di poterla far variare per patch).

Ordine di attacco consigliato quando si riprende: 1) griglia graduata
(non octree) — rischio più basso, riusa la struttura a box esistente;
2) refinement per regione sopra la griglia graduata; 3) BL nativo per
ultimo, è l'unico dei tre che richiede un algoritmo nuovo da zero.
