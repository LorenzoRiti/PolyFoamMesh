# Crash GMSH su CAD periodico + bug della mesh-sonda

Sessione dedicata a: far funzionare bene il percorso poly "in pochi click",
sistemare il crash riprodotto su `Parte4.stp`, e togliere codice morto.

## 1. Il crash: causa vera e tre fix concatenati

### Diagnosi (verificata ispezionando il B-Rep, non supposta)

`Parte4.stp` ha superfici periodiche (Cone/Cylinder/BSpline) il cui seam OCC
compare **due volte** nel proprio contorno — legittimo per una superficie
periodica, ma è il punto in cui il recovery delle intersezioni di curva di
GMSH non converge. Verificato con `getAdjacencies`:

```
surface 85 (Cylinder): curves=[220, 221, 222, 223, 221]   <- 221 due volte
surface 86 (BSpline):  curves=[224, 225, 224, 222, 223]   <- 224 due volte
surface 88 (Cylinder): curves=[226, 227, 228, 227]        <- 227 due volte
surface 69 (BSpline):  curves=[188, 136, 138, 140, 141, 188, 189]
```

Con Frontal-Delaunay il loop di recovery escalation ("N intersections in the
1D mesh", livelli crescenti) termina in **segmentation fault** — un crash del
processo C++, NON un'eccezione Python: l'app non può né rilevarlo né
recuperarlo.

### Fix 1 — algoritmo 2D: MeshAdapt invece di Frontal-Delaunay

Misurato su run ripetuti: MeshAdapt attraversa le stesse superfici ma
recupera in 1 iterazione nella maggior parte dei casi, e dove fallisce
solleva un'eccezione Python normale e catturabile. Mai osservato peggiore su
nessun caso di test (cilindro, venturi, box).

### Fix 2 — ORDINE: le opzioni algoritmo vanno impostate PRIMA del sizing

Il Fix 1 da solo **non aveva alcun effetto**. Il percorso adattivo chiama
`_sample_passage_thickness_field` → `_extract_boundary_trimesh`, che esegue
`mesh.generate(2)` *dentro* la configurazione del sizing — quindi la
meshatura 2D (dove avviene il crash) girava prima che l'algoritmo fosse
impostato. Misurato dal log: **314 superfici con Frontal-Delaunay contro 32
con MeshAdapt** (queste ultime tutte fallback interni di GMSH), cioè
l'algoritmo richiesto non si applicava mai al passaggio che conta. Dopo lo
spostamento: **321 MeshAdapt, 0 Frontal-Delaunay**.

### Fix 3 — la mesh-sonda va grossolana, e va buttata via

`_extract_boundary_trimesh` genera una mesh 2D solo per avere qualcosa contro
cui fare ray-casting. Due bug distinti, entrambi introdotti dalla feature del
campo di spessore in questa stessa sessione:

**3a — la sonda veniva RIUSATA come mesh finale.** GMSH riusa una mesh 2D
esistente quando `generate(3)` parte dopo. Verificato direttamente: un cubo
meshato a lc=0.5 e poi generato a lc=0.08 **manteneva tutti i 540 triangoli
originali**. Conseguenza: la superficie finale restava congelata al sizing
che esisteva PRIMA che qualsiasi campo fosse installato, buttando via in
silenzio tutto il sizing (passaggio, curvatura, curve piccole) che quella
funzione esiste per calcolare. Fix: `mesh.clear()` dopo l'estrazione — dopo
il fix, 540 → 2424 triangoli. Test di regressione:
`test_probe_mesh_is_discarded_so_size_fields_still_apply`.

**3b — la sonda girava a piena risoluzione.** Essendo un secondo passaggio 2D
completo, raddoppiava il costo del 2D per un'informazione (la larghezza
locale del passaggio) che non ha bisogno di risoluzione. E la faceva proprio
dove GMSH è fragile. Fix: la sonda usa `h_max` come dimensione massima,
ripristinando poi il valore originale. Effetto misurato su `Parte4.stp`:
il 2D della sonda **passa in 1.25 s** dove prima il processo restava nel loop
di recovery per minuti.

## 2. Codice morto rimosso (solo quello con ZERO riferimenti)

Verificato uno per uno su `src/`, `tests/`, `tools/` e sullo `.spec` di build:

- `scratch_cfmesh_valve_test.py`, `scratch_defeature_test.py`
- `metagpt_spec.json`, `metagpt_impl_result.json`, `save_spec.py`
- non tracciati: `scratch_split_ab*`, `log.mesh`, `tools/_tmp_dbg_term2.py`,
  `VoroCrustLog.txt`

**NON rimossi**, benché sembrino morti — verificati raggiungibili:
- `rthook_casadi_dlls.py` → usato da `CFMesh-AutoGUI.spec` (`runtime_hooks`)
- `adaptive_cli`, `cloud_mesh`, `mosaic`, `snappy_hex_mesh`,
  `native_poly_bridge`, `disk_cleanup` → pochi riferimenti, tutti reali
- `autopoly/` (sorgenti C++) → mai compilato qui, ma `autopoly_bridge.py` lo
  importa con fallback Python esplicito: cancellarlo sarebbe rimuovere una
  feature, non codice morto
- `throat_detector` → checkbox nascosto, percorso ancora raggiungibile

## 3. Decisione presa: `collapse_smooth_edges` resta SPENTO

Il collasso delle facce duali di bordo (implementato in una sessione
precedente, `tet_poly_dual.py`) darebbe ~6× meno celle prisma nel BL e la
topologia STAR-CCM+ vera. **Non l'ho attivato di default**: la misura sul
mio stesso test lo sconsiglia — su superficie curva i poligoni collassati
sono non planari e i difetti circa **raddoppiano** (96 → 198 sulla sfera di
test, 138 → 264 a risoluzione maggiore). Per l'obiettivo "una mesh come si
deve" sarebbe un peggioramento, non un miglioramento. Resta disponibile come
opzione esplicita, documentata con i suoi numeri.

## 4. NON fatto / da verificare

1. **checkMesh reale (WSL) prima/dopo** su venturi/valvola per quantificare
   l'effetto dei fix sulla qualità finale — non eseguito per non contendere
   CPU/WSL con l'altra sessione.
2. **Il crash non è eliminato al 100%** su `Parte4.stp`: la degenerazione del
   B-Rep è reale e GMSH si comporta in modo non-deterministico da un run
   all'altro sullo stesso file (verificato con run ripetuti a parità di
   parametri). I fix riducono drasticamente probabilità e costo, e
   `main_window` ora riprova a un livello più grossolano invece di ripetere
   lo stesso tentativo identico.
3. **Riparazione STEP via OCC ShapeFix**: prototipata e funzionante
   (`ShapeFix_Shape` + `ShapeFix_Wireframe.FixSmallEdges` via OCP, che è
   **già** una dipendenza — nessun pacchetto nuovo), scrive uno STEP riparato.
   Non integrata nell'app: il test di meshatura sul file riparato non è stato
   portato a termine, quindi non ho la prova che risolva davvero. È il
   prossimo passo naturale se il crash si ripresenta.
   Prototipo: vedi la cronologia di questa sessione (ShapeFix su
   `Parte4.stp` → `Parte4_healed.stp`, 11029 entità scritte).
