# GOAL

vedi cosa c'è nell'app e capisci come migliorare gli algoritmi di meshing per renderli commerciali, siamo gia abuon punto funzioanno esa cf mesh con layer tetra epoly propietario con layer , vedi tu come migliorare

## Success criteria
- Algoritmi di meshing portati a livello commerciale: cfMesh (cartesian + BL), percorso tetra GMSH, dual poly proprietario (tet→poly) con layer
- Ogni miglioramento misurato con benchmark A/B (celle, tempo, checkMesh: non-ortho/skew/aspect/neg volumes, y+ realizzato) prima/dopo
- Nessuna regressione: ruff green · pytest `-m "not slow and not wsl"` green
- Nessun default cambiato senza flag o validazione
- Robustezza commerciale: nessun freeze/crash, fallback chain completa, progress/ETA affidabili

## Status
- Created: 2026-09-02
- Nota: goal precedente ("bug + UX", creato 2026-08-23) sovrascritto

## Avanzamento 2026-09-02

Baseline: ruff green · 1045 passed / 3 skipped / 1 xfailed.

Review indipendente (oracle) sulle priorità: ha **killed** 3 ipotesi
(per-vertex fade del BL già implementato in `bl_poly.py:825-839`; octree
nativo; predizione conteggio celle) e ha individuato il rischio vero:
l'escalation sostituisce l'algoritmo (hex→tet) con `success=True` e nessun
consumer leggeva i campi che lo registravano.

Realizzato e verificato (suite a 1126+ passed, ruff green):
- **Sostituzione algoritmo resa visibile** — `MeshEngineResult.algorithm_substituted`
  + propagazione a QuickMesh / FullAuto / report JSON / suite A/B.
  Accertato: `adaptive_escalation=True` e `max_escalation_steps=3` sono i
  default, quindi la sostituzione era il comportamento predefinito.
- **Boundary layer mai più su inlet/outlet** — `poly_bl_patch_selection()`
  in `openfoam_runner.py`: tolto il fallback "applica a tutte le patch", che
  estrudeva prismi negli ingressi (checkMesh lo accetta, diverge il solver).
- **`core/patch_roles.py`** — fonte unica per "questa patch è una parete?",
  prima risposto in 5 copie divergenti. API a due livelli:
  `classify_patch()` (default parete, per decisioni obbligate) e
  `match_role()` (None = nessun segnale, così la geometria decide).
- **Remediation qualità sul percorso poly** — `commercial/poly_remediation.py`:
  prima analizzato e poi abbandonato a sé stesso. Loop keep-best, strettamente
  non-regressivo, con skip della classe di difetti concavi documentata.
- **Fault-tolerant non più stub** — volume fill reale con fallback BL→no-BL;
  `success=True` ora garantisce una mesh su disco validata da checkMesh.
- **Onestà API** — parser ASCII STL reale al posto di `NotImplementedError`;
  cloud offline con stato `NOT_SUBMITTED` invece di `PENDING`.
- **Bug**: `MeshEngine(of_config)` sollevava `TypeError` (raggiungibile dal
  pulsante Solve Adaptive). `MeshEngine.__init__` allineato a
  `ParallelMeshEngine`.
- **Fragilità test**: `test_mesh_engine.py` passava solo se eseguito dopo un
  altro file; corretto il pre-load in `_test_helpers.py`.

Nota: la GUI principale non passa da `MeshEngine`, quindi l'utente GUI non
vede ancora l'avviso di sostituzione — remaining work documentato in
`docs/residual_risks.md`.

## Avanzamento 2026-09-08 (notturno)

CI rimessa verde su GitHub Actions (era rossa su ogni push). Causa NON la
rinomina in sé ma ciò che la rinomina ha esposto sul runner Linux:
`pytest-qt` fuori dagli extra (era già corretto nel tree, mai spinto),
percorsi stile Windows distrutti da `Path.resolve()` su POSIX
(`config.py`, `core/validation.py`), dipendenze trimesh usate ma non
dichiarate (`networkx` per il CAD healing, `rtree` per native_mesher).
Suite fast verificata verde su Linux (mirror WSL di ubuntu-latest) e su
Windows prima di ogni push.

NOTA STORIA GIT: il push di `master` falliva da sempre per un blob da
278 MB (`installer/output/*.exe`) raggiungibile dalla storia. Storia di
`master` riscritta con filter-branch (solo range `origin/main..master`,
297 commit preservati, alberi verificati identici). Branch locale di
backup: `backup-master-pre-rewrite` — gli SHA vecchi referenziati nelle
note puntano lì. Remoto: `master` nuovo branch su origin (main NON toccato).

Bug reali corretti (con test di regressione):
- Zone di raffinamento manuali: `setAsBackgroundMesh` scartava il campo
  adattivo geometrico; ora MIN-combinato (`gmsh_wrapper.py`,
  `_combine_zone_fields_with_bg`) — vedi residual_risks §Sizing.
- Corpi curvi chiusi (sfera) tessellati non-watertight: vertici coincidenti
  a cuciture/poli ora fusi e triangoli degeneri scartati
  (`geometry.py::tessellate_patches`) — vedi residual_risks §Sizing/Estimates.
- Test smette di sovrascrivere `sample_cad/` tracciato; stub sempre-skip
  `test_analyze_step_with_mock` sostituito con test d'integrazione reale.

Aperti per conferma umana: avviso GUI su sostituzione algoritmo (richiede
decisione di prodotto: la GUI non passa da MeshEngine), validazione SAMR
sulla valvola (ore di calcolo WSL), integrazione ShapeFix per Parte4.stp
(prototipo esistente, non validato).

## Avanzamento 2026-09-09 — FASE 2: BL valido sulla concava

Obiettivo della fase: chiudere il boundary layer sulla geometria concava di
riferimento (valvola), dove il percorso standard fallisce a ogni scala.

Causa radice trovata e risolta (misurata, non assunta):
- La riparazione del winding (orientamento facce) era greedy per-cella e
  restava in minimi locali: 24 celle non chiuse a scala 1.0 che
  degeneravano in un flip globale a scala 0.6 (378.605 volumi non
  positivi). Sostituita da `_solve_global_windings`: vincoli di parità per
  lato risolti esattamente via BFS + segno di volume per componente.
  Deterministico, O(F+E): sulla valvola 0 celle non chiuse (max_rel
  ~1e-12), volume conservato a 1e-16, nessun flip.
- Nuovo opt-in `run(local_termination=True)`: terminazione locale BINARIA
  (faccia = stack pieno o esclusa) + esclusione iterativa delle facce con
  prismi invalidi, fino a 5 scale x 3 round, mesh scritta solo se il gate
  passa.

Risultato end-to-end sulla valvola (1.05M punti, 152k celle, 226.542 facce
di parete; n_layers=2, h1=1e-5, dual_convexity): successo alla scala 0.1,
19.625 facce escluse (8.7%), 410.668 prismi, volume conservato, 1602 s.
checkMesh reale: 891 facce male orientate vs 895 dell'input (MIGLIORE),
non-ortho e skew quasi invariati; un check in più (aspect ratio, intrinseco
ai prismi sottili). Default invariato: `local_termination=False`.

Bug collaterale trovato e corretto durante la verifica:
`foam_mesh_io.read_label_list` scambiava per ASCII i payload binari di
gmshToFoam contenenti un byte 0x29 → lista vuota → polyMesh corrotto al
write successivo (riprodotto al 100%). Fix + regression test.

Verifica: ruff green · fast suite 1151 passed / 2 skipped / 1 xfailed ·
slow valvola (path standard) 1 passed 473 s · gate FASE 1 WSL PASS
(cilindro e cubo duct, `Mesh OK`).

## Avanzamento 2026-09-09 — FASE 3: generalizzazione (addendum)

Ragionamento e previsioni scritti PRIMA dei test
(`notes/fase3_reasoning.md`), poi misure su 4 geometrie con checkMesh
reale. Esito: `local_termination` non peggiora nulla dove il percorso
standard funziona (cilindro/cubo: no-op, 0 escluse) e sblocca il BL dove
il percorso standard fallisce o produce mesh bocciate da checkMesh:
groove1 (standard = 1 layer + checkMesh Failed 2 → LT = 3 layer + Mesh OK)
e slot1 (standard fallisce del tutto → LT = Mesh OK, 0,8% di parete
scoperta). Predittore economico pyr_input + vertici a fade<0.5 che ordina
la difficoltà (0/0, 4/2, 42/17, 1.007/1.123). Finding principale: sulla
valvola l'8,7% di facce escluse è per la maggior parte un artefatto
dell'ordine delle scale (thick-first); partendo da 0.1 servono 2.058
esclusioni (9,5x in meno) e si conserva l'8,5% di prismi in più.
Raccomandazione (non implementata, nessun default cambiato): invertire
l'ordine delle scale o azzerare le esclusioni scendendo di scala.
Dettagli e numeri completi in `docs/residual_risks.md` §FASE 3.

## Prossimo passo — thin-first generalizza oltre la valvola? (ESEGUITO)

Compito eseguito — vedi Avanzamento FASE 4 sotto e
`notes/thinfirst_reasoning.md`. (Il brief originale è stato potato
nell'igiene pre-push: era superato dall'esito registrato qui.)
Riassunto: la scoperta thin-first (9,5x meno esclusioni, +8,5% prismi,
~5x più veloce) è stata misurata solo sulla valvola — ripetila su
cilindro/cubo/groove1/slot1 con lo stesso metodo (previsione scritta
PRIMA del test, poi misura, poi numeri onesti in
`docs/residual_risks.md`). Se non generalizza è comunque un risultato
valido da scrivere. Nessun default cambiato, nessun wiring GUI finché
l'utente non ha validato personalmente FASE 2 sulla valvola.

## Avanzamento 2026-09-09 — FASE 4: thin-first NON generalizza (fatto)

Risposta misurata: **no, come inversione cieca non è proponibile**. Su
tutte e 4 le geometrie thin-first valida al primo tentativo (scala 0.1)
e riduce in silenzio il `first_height` richiesto di 10x (h1 effettivo
5e-4 invece di 5e-3): stessi prismi e 0 esclusioni su
cilindro/cubo/groove1 ma aspect max 6-10x peggiore (es. cubo 78,7 ->
781,6); su slot1 il vantaggio valvola si rivede (0 vs 132 esclusioni
iterative, +390 prismi, 2x più veloce) ma di nuovo a spessore 10x
ridotto. La valvola resta l'unico caso dove thin-first non danneggia
(0.1 era comunque l'unica scala valida). Previsione FASE 4 sbagliata
nello spessore (dichiarato nel file di ragionamento). Variante da
misurare in futuro: prova la scala richiesta per prima e RESETTA le
esclusioni scendendo di scala (decoupled) — non implementata, nessun
default cambiato. Numeri e ragionamento in `docs/residual_risks.md`
§FASE 4; bench riproducibile in `tools/bench_bl_thinfirst.py`.

## Avanzamento 2026-09-09 — FASE 5: variante decoupled implementata e misurata

`run(local_termination="decoupled")` (opt-in, default invariato):
scala richiesta per prima, esclusioni azzerate al cambio di scala.
Obiettivo centrato: su cilindro/cubo/groove1/slot1 identico al legacy
carry (scala 1.0, spessore pieno, stessi prismi/esclusioni/aspect);
sulla valvola 2.058 esclusioni e 445.716 prismi esattamente come il
thin-first (9,5x in meno del legacy, +8,5% prismi, checkMesh 885 vs 891)
ma con lo spessore contrattuale rispettato. Costo: 2.215 s sulla valvola
(più lento del legacy, 1.602 s: prova tutte le scale). Previsione
scritta prima in `notes/decoupled_reasoning.md`; test nuovi in
`tests/test_bl_poly.py`; fast suite 1153 verdi. Resta candidata (non
default) per quando l'utente abiliterà la terminazione locale.

## Avanzamento 2026-09-09 — FASE 6: early-exit-intermediates (ottimizzazione tempo)

Domanda: un early-exit riduce il tempo sulla valvola? Risposta misurata:
l'engine ha GIÀ un early-exit alla prima scala valida (cylinder/cubo
fanno 1 sola build, 13-17 s). Il tempo lungo della valvola (37 min nel
decoupled, 15 build) è dovuto al fatto che solo 0.1 valida. Nuova
variante opt-in `run(..., early_exit_intermediates=True)`: se 1.0
round 0 fallisce, salta 0.6/0.35/0.2 e va diretto a 0.1. Sulla valvola
stesso risultato (2.058 escl, 445.716 prismi, checkMesh 885) in **414 s
invece di 2.215 s (5,4x più veloce)**, 4 build invece di 15. Rischio
documentato: degenera in thin-first su slot1 (scala 0.1 invece di
1.0, aspect 128 invece di 17). Euristica candidata *solo* per
geometrie tipo valvola. Default invariato, test aggiunto, fast suite
1154 verdi. Numeri in `docs/residual_risks.md` §FASE 6.

## Avanzamento 2026-09-09 — FASE 7: topologia di produzione + fixture stale

Scoperto che la produzione (`openfoam_runner.py:1652`) usa il dual
**collapsed** mentre tutte le misure BL FASE 2-6 usano il converter con i
default (collapse OFF). Misurato con
`tools/bench_bl_production_topology.py`: cilindro collapsed = 4.412
facce bordo (5,65x meno), BL ok (13.236 prismi, Mesh OK). Valvola
collapsed: 42.130 facce bordo, 340 difetti input (vs 863), ma la BL
**FALLISCE** (decoupled, 15 build, 892,7 s, stallo 375-444 piramidi vs
370). Inoltre la fixture valvola è **stale** (895/44,3 del 2026-08-01 vs
863/14,6 fresca): i numeri valvola FASE 2-6 sono pinned a uno snapshot;
sul converter attuale la valvola exact valida a scala 1.0 con 2.525
esclusioni. Trovato e corretto un bug dell'early-exit FASE 6 (saltava i
round 1-2 della scala 1.0, mascherato dalla fixture stale e causa della
degenerazione su slot1). Nessun default cambiato. Dettagli in
`docs/residual_risks.md` §FASE 7.

## Avanzamento 2026-09-09 — Lane C (verifica, risultato onesto)

La lane C del MEGA_PLAN ("GUI substitution notice") è stata verificata
con un grep nel codice GUI (`src/polyfoammesh/gui/`): **0 chiamate** a
`MeshEngine().run`, `QuickMesh().run` o `engine.run`. La GUI usa
`engine.auto_select()` (scelta iniziale, no escalation) e poi
`RetryRunner`/`cartesianMesh` (no `MeshEngineResult`). Quindi la GUI
non produce mai `algorithm_substituted` da `MeshEngine`: non c'è
substitution algoritmo da loggare nel log GUI. Le 5 substitution/
fallback reali della GUI (BL off, detail, Gmsh algorithm, parallel→
serial, BL disabled) sono già coperte da `_log_substitution`
(`main_window.py:1750`) e dai 4 test in
`tests/test_gui_substitution_notice.py` (verdi). Risultato: Lane C è
effettivamente chiusa per la GUI corrente; il "rimanente lavoro" nel
residual era obsoleto. Aggiornato `docs/residual_risks.md` con la
verifica e nota onesta. Nessuna modifica al codice.

## Avanzamento 2026-09-09 — FASE 9 / H4: esclusioni per-vertice, valvola collapsed CHIUDE

Refactor strutturale del modello di local termination: nuova modalita'
opt-in `local_termination="decoupled_vertex"` (default invariato).
L'unita' di esclusione iterativa e' il VERTICE di parete invece della
faccia: un prisma invalido esclude tutti i vertici della sua base, e
una faccia e' estrusa solo se nessun suo vertice e' escluso. Sul dual
collapsed di produzione (una poligonale di bordo = stella di un
vertice) la propagazione chiude in un round cio' che il modello
per-faccia non chiudeva in nessun round. Misura: valvola collapsed di
produzione, success=True a scala 0.6, 4.008/42.130 facce escluse
(9,5%), 75.360 prismi, 5 build, ~297 s; checkMesh reale con 340 facce
male orientate = ESATTAMENTE l'input (zero aggiunte), Failed 4 vs 3
(extra = aspect ratio intrinseco). Baselines per-faccia (FASE 7/8):
stallo 373-385 vs 370, fallimento. Unit test fast + regressione slow
`tests/test_bl_collapsed_valve.py` (skip se tet backup assente).
Nessun default cambiato. Dettagli in `docs/residual_risks.md` §FASE 9.

## Avanzamento 2026-09-15 — Lane A: root-cause RAM chiusa, guard in facce

Run sintetico reale a ~5M celle (hex 200x160x156 = 4.992.000 celle /
15,06M facce, generato in locale): picco 13,05GB (build) e 10,6GB
(merge), sotto la barra 16GB. Cause radice misurate e fixate: grafo di
parità whole-mesh + adjacency dict (~2,4KB/faccia) -> batching per
range di celle + CSR (~10B/lato); transienti densi di `_face_geometry`
(bl + tet, ~5-7GB/call) -> blocchi da 250k facce bit-identici;
`vert_faces` su tutti i vertici -> solo vertici di parete; copia
integrale nel winding solve -> flip in place; transienti skew del merge
(`_face_geometry`/`_face_extent` tet) -> chunked (merge 18,1 -> 10,6GB).
Soglia spostata da 1,5M celle a 8M facce (la memoria scala con le
facce: dual ~8,2/cella vs hex ~3,05; fit dual a due punti; dual-5M da
41M facce resta skippato). Limite strutturale documentato: dual-5M
proietta ~50GB, serve riscrittura compact-face (deferita). Commit
`d414047` + `bc6c450`. Dettagli in
`notes/ram_crash_rootcause_reasoning.md`. Nessun default cambiato
(guardia di sicurezza allargata su base misurata, opt-in invariato).

## Avanzamento 2026-09-15 — Lane B: combinato most_visible + height_retry

Run valvola pristine con `normal_method="most_visible"` +
`local_termination="decoupled_vertex"` + `local_height_retry=True`
(n_layers=2, first_height=1e-5, growth 1.2): success=True a scala 1.0,
6195 facce escluse — batte ENTRAMBE le leve singole (6612/6704 da
baseline 7054, -12,2%), complementarità confermata come predetto.
checkMesh reale OF2512: profilo = envelope di most_visible (wrong 847,
skew 16.805, aspect 981 identici) + costo retry visibile (short edges
299) + una regressione onesta oltre le singole (severeNO 10411 vs
10117, +2,9%). Promosso a combinazione opt-in documentata, non default.
Dettagli in `notes/combined_normal_height_retry_reasoning.md`.

## Avanzamento 2026-09-15 — Lane C: smoothed+dual sbloccato, no-op misurato

Il `raise` era stantio (il blend legge già `angle_fade` = `dual_fade` in
modalità dual): sbloccata la combo per solo "smoothed" (most_visible+dual
resta rifiutato, genuinamente non implementabile). A/B valvola + checkMesh
reale: esclusioni 6470 -> 6455 (-0,23%, sotto la barra), checkMesh
identico. NON promosso, documentato come no-op (il dual fade termina già
tutto ciò che lo smoothing potrebbe salvare). Commit `ac8a3e4`. Dettagli
in `notes/smoothed_dual_blend_reasoning.md`. Nessun default cambiato.

## Avanzamento 2026-09-15 — Lane D: cluster aspect mappati, trial respinto

Mappa per-cella sul cilindro: cluster tappi (z≈±0.995) + parete esterna
(r≈0.49) confermati, termine direzionale ovunque; la peggiore è una
lastra sottile al tappo (allungamento in-plane). Trial greedy di merge
mirati su copia scratch: 12 merge, max 4.489 -> 4.013 in-process, poi
muro di validità. checkMesh reale RESPINGE: NOmax +80%, skew +233%,
failed 1 -> 3 (stesso pattern lam=10/slot1: il gate conta violazioni,
non severità). NON promosso; operatore di SPLIT direzionale scoped come
lavoro futuro. Nessun codice di produzione toccato. Commit `5854e18`.
Dettagli in `notes/cylinder_aspect_clusters_reasoning.md`.

## Avanzamento 2026-09-15 — Lane C2: guided filter da letteratura, no-op di principio

Nuovo `normal_method="guided"` (Zhang et al. 2015, bilaterale guidato sui
normali di faccia + media ai vertici), con test di riferimento
indipendente e ridge-preservation verdi. Valvola: 7054 esclusioni =
baseline al decimale; slot1: 510 = 510. Meccanismo: i normali dual non
sono rumorosi (il problema che guided risolve) e al ridge il vertice
media comunque tra i lati. NON promosso, resta opt-in documentato.
Commit `5047c71`. Dettagli in `notes/guided_normal_filter_reasoning.md`.
Nessun default cambiato.

## Avanzamento 2026-09-15 — Lane D2: rilassamento zonale worst-element

Nuova modalità opt-in di `smooth_dual_mesh` (Freitag-style minimax:
mosse mascherate sulla zona + termine L_inf sul max, con test di
pinning). Lungo la via catturati e fissati due errori veri (candidati
mascherati valutati per ultimi = mai eseguiti; somma sulla coda che
alzava il max). Cilindro + checkMesh reale: max 4.489 -> 4.468 (-0,5%)
a costo zero, muro confermato a 3x iterazioni (topology-bound). NON
cablato in produzione (guadagno sotto rumore); resta lo SPLIT come fix
scoped. Commit `1061a88`. Dettagli in
`notes/zonal_aspect_relaxation_reasoning.md`. Nessun default cambiato.

## Avanzamento 2026-09-17 — pre-push wrap-up

- Parte 1 (igiene): 10 record di decisione committati, 10 scarti potati
  (6 brief, lane_c_verification, overnight_plan eseguito, MEGA_PLAN
  stantio, scratch commit_split_rounds), 2 dangling pointer riparati.
  Commit `9d1e6d1`, tree pulito.
- Parte 2 (verifica piena): full suite 1206 passed / 2 skipped
  (venturi-throat, tessellation-dependent) / 1 xfailed preesistente /
  0 failed in ~14 min; `ruff check src/ tests/` pulito. Zero regressioni.
- Parte 3 (handoff): `docs/dev/STATO_PRE_PUSH_2026-09-17.md` — temi dal
  9/9, default dichiarati invariati con prova, tabella opt-in con numeri,
  lista scarti onesti, limiti residui, ambito non toccato (installer, CI,
  commercial, pyproject: zero commit dal 9/9 verificato).
- Parte 4 (split): NON tentata per decisione — uno split affrettato
  rischiava l'albero verde a ore dal push; taskata al collega notturno
  con prompt blindato + barriere. Resta l'unico lavoro strutturale aperto,
  con specifica pronta.
- Mai pushato, mai toccato origin/main (qui non esiste nemmeno il remote).

VERDETTO: **pronto per il push.** L'albero è verde, i default invariati,
ogni numero dichiarato ha una misura dietro e i limiti sono scritti
nell'handoff §5 (gap aspect cilindro, soglia RAM misurata solo su hex,
validazioni pesanti solo WSL). Leggi §5 prima di decidere: se uno di quei
limiti ti è inaccettabile sul pubblico, quello — e solo quello — blocca.
