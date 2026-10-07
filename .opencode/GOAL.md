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
validazioni pesanti solo su WSL). Leggi §5 prima di decidere: se uno di quei
limiti ti è inaccettabile sul pubblico, quello — e solo quello — blocca.

## Avanzamento 2026-10-05 — Lane NON-ORTHO poly (dual-side): KILLED, onesto

Correzione di rotta umana: il bersaglio della lane è il NOSTRO motore
tet→poly (GMSH tet → dual + BL), non cfMesh. Diagnosi misurata (probe
read-only, nessuna modifica): il dual da solo è a 51.9 (cube_duct, PASS) /
74.0 (venturi) / 62.7 + 36 piramidi (concave_bracket); il BL lo sfonda a
80.6 / 146.4 / 105.4, con prismi-sliver (V ~1e-12) generati dalle 83 facce
di bordo del dual sotto 1e-6 m². Patch selection = no-op; decoupled_vertex
non basta (85/81/89).

Fix implementato opt-in: dissoluzione degli star di bordo minuscoli dentro
la walk (default off, keep-best, 4 task + review indipendenti). Parte su
tutte e tre le geometrie (171/97/163 star a frazione 0.5) ma OGNI build è
rifiutato dal gate: closure rotta su cube_duct (2.94e-1) e concave (3.74e-2),
piramidi 84 vs 15 su venturi; a frazione 0.25 idem. Nessuna metrica si muove
e mai è stata scritta una mesh che fallisse closure/volume/piramidi — il gate
keep-best copre quello (non confronta non-ortho/skewness) e ha fatto il suo
lavoro.

VERDETTO: **KILLED, non promosso** (precedente: lane misurate e scartate).
Prossimo passo scoped, a scelta umana: (a) la causa radice della splice
(chord di chiusura con nf ≥ 3) è stata CORRETTA il 2026-10-05: la closure
regge, ma il gate piramidi resta bloccante e STRUTTURALE — le celle fuse
sono non-convesse per costruzione (170 vs 0 / 84 vs 15 / 139 vs 36). Il
revival richiede rilassamento dei punti durante il merge o un'operazione
topologica diversa, non un fix della splice; (b) attaccare il dual di
venturi (74.0) e le piramidi di concave con le leve già misurate
(compactness/axis, wedge cells). Dettagli in
`notes/poly_nonortho_reasoning.md` §B-side; piano e review in
`docs/superpowers/plans/2026-10-05-poly-dual-boundary-star-dissolution.md`.

## Avanzamento 2026-10-05 — baseline vs snappy + prima lane NON-ORTHO

Baseline ufficiale full-tier locale (10 geometrie × 5 runner = 50 record,
commit `b1bc71f`, `benchmarks/results/LEADERBOARD.md`): pass rate **hex 5/10 ·
poly 4/10 · gmsh_direct 5/10 · snappy_base 6/10 · snappy_expert 6/10**.
Nessun runner passa dirty_cad (0/5) né mini_valve (0/5); snappy vince 5 celle
su 10 (expert 3, base 2), hex 3 (cylinder, venturi, multi_inlet).
Pass rate **fast tier** (6 geometrie × 5 runner, re-run
`comparison_20261005T135839.json` con la sentinella di timeout corretta):
**hex 3/6 · poly 4/6 · gmsh_direct 4/6 · snappy_base 6/6 ·
snappy_expert 5/6** (l'unica differenza rispetto al vecchio fast baseline è
`thin_gap/snappy_expert`, ora `timed_out: true`).

Top 3 gap (`benchmarks/results/GAP_ANALYSIS.md`, commit `78cff6f`, 31 righe):
1. **`max_non_ortho`** — 15 righe su 31 (48%), 8 geometrie su 10, e contiene
   TUTTE e 9 le nostre threshold-miss di qualità (hex 4, poly 3, gmsh 2):
   è l'unico cluster che decide il gate.
2. **`max_skewness` relativo** — 6 righe / 4 geometrie; le due nostre righe
   di skew che falliscono il gate cadono sempre dentro una miss non-ortho,
   quindi qui restano solo perdite relative (es. groove1 poly 3.67 vs 0.65).
3. **`cells_produced` = 0** — 7 righe / 3 geometrie ma quasi tutte condivise
   (dirty_cad 0/5, mini_valve 0/5 anche per snappy; l'unica competitiva è
   multi_inlet, dove hex vince). Più 3 timeout — tutti di snappy, mai nostri.

Lane scelta: **NON-ORTHO**. Primo bersaglio: **cluster hex** — groove1 75.98,
slot1 77.28, thin_gap 73.75, mini_valve 72.58 contro gate 70, mentre snappy
sta a 39.40–42.71 sulle stesse geometrie. Scelta perché 4 gate-flip in un solo
percorso (vs 1 riga del peggio assoluto venturi/poly 146.41), tutte e 4
falliscono **solo** `non_ortho_max` → un solo fix porta hex 5/10 → 9/10, sopra
il 6/10 di entrambi gli snappy; 3 delle 4 righe sono fast tier.

Predizione (scritta PRIMA di ogni esperimento in
`notes/non-ortho_reasoning.md`): il percorso hex emette un meshDict con solo
`maxCellSize/minCellSize` — nessuna direttiva di raffinamento agganciata alla
feature sottile — quindi dove lo scanalo/slot è più sottile di `max_cell`
(0.85 / 0.32 / 0.43 / 0.27 celle across in groove1/slot1/thin_gap/mini_valve)
una cella copre entrambe le pareti e la proiezione produce le facce >70° che
checkMesh riporta. Con un fix dietro flag opt-in `thin_feature_refinement`
(default off) groove1 hex deve scendere **75.98 → 50–68 (<70)** a costo ≤3×
celle e ≤2× tempo, con cylinder/cube_duct/venturi **identici** (la direttiva
non si attiva). A-side misurata e registrata:
`comparison_20261005T123603.json` (groove1/slot1/thin_gap) +
`comparison_20261005T123710.json` (mini_valve) — tutte e 4 falliscono solo
`non_ortho_max`, coerente con il baseline.

Il fix è un **piano da approvare** (follow-up): in questo turno ZERO modifiche
al mesher, nessun default cambiato; si ferma qui e si riporta all'umano.


## Avanzamento 2026-10-06 — Programma poly "beat snappy" (notturno): 4 fasi, tutte KILLED con causa radice trovata

Eseguito il piano `docs/superpowers/plans/2026-10-05-poly-beat-snappy-program.md`
(4 fasi + integrazione, 10 task, tutto opt-in, default invariati, nessun push).
Risultato onesto: **nessuna leva promossa**, ma la diagnosi è ora completa e
quantificata.

- **Fase 1 — filtro facce base sliver** (`min_base_face_fraction`, 675ee33):
  KILLED. Rimuove 140 facce ma cube_duct resta 80.61 e skew/aspect peggiorano
  (7.77/181): le bande terminatrici fanno danno e la faccia da 80.6 non è un
  seme sliver.
- **Fase 2 — config dual** (compactness/axis/iterazioni/triangulate, 8cfae3a,
  2ef8c1e): KILLED sul pipeline completo (no-op o peggio: il BL maschera
  tutto). Isolamento dual-only: cube_duct NO 51.9→47.3 ma skew 3.93→4.59
  (oltre gate); venturi/concave no-op. `triangulate` migliora lo skew di
  cube_duct (4.92→2.74) ma distrugge venturi/concave (skew 30-48).
- **Fase 3 — riparazione piramidi concave** (`--poly-merge-repair`, 25a8b39):
  KILLED. concave fixed 68/81, NO 105→92.8; venturi 12/33, 146→136; ma
  groove1 peggiora 50.6→65.8 (skew 5.5) — la regressione che il gate
  in-process non vede. Regola "nessuna riga che passava può fallire" → no.
- **Fase 4 — margini / clamp**: scoperta della causa radice. Il clamp
  per-vertice `0.5 * min_edge[w]` (edge minimo delle facce di parete, ≈1.4e-4
  su venturi) cappa l'intero stack a ~7e-5 m e il fixpoint inonda l'intera
  shell di pancake; `clamp_factor` è inerte (0.5/1/2 identici) e `h1`×10 non
  cambia nulla. Leva `edge_cap_percentile` (48966c1): KILLED — rilasciare gli
  sliver fa fallire la validazione (cube_duct perde il BL del tutto, venturi
  e concave invariati).

**Conclusione per il futuro:** il divario con snappy non è nel dual (che su
cube_duct passa da solo a 51.9) ma nel BL sul boundary collassato: edge
minuscoli + celle di bordo sottili rendono i prismi pancake/sliver qualunque
leva si giri. Serve un intervento topologico dual-side sul boundary (edge
collapse/decimazione con rilassamento punti), non altri knob. Tutte le
evidenze nelle note `poly_*_reasoning.md` e nei JSON committati.

## Avanzamento 2026-10-06 — BREAKTHROUGH: poly a parità con snappy (opt-in)

Continuando oltre i gate del piano (richiesta umana), due leve mirate hanno
risolto il danno BL: filtro per **min-edge** delle facce base
(`--poly-bl-min-edge 0.9`, commit a94737f) + **terminazione per budget**
(`--poly-bl-budget-term`, 19286b3: i vertici stretti riducono i layer a
piena dimensione invece di comprimere lo stack in pancake).

Full tier poly con la combo: **6/10 PASS (era 4/10, snappy 6/10) = parità**,
con **cylinder 38.13 vs snappy 52.96 (MEGLIO di snappy)**; cube_duct
80.6→58.9 PASS, concave 105.4→66.8 PASS, venturi 146.4→74.2 (ora pari al
dual-only: il BL non aggiunge più danno; resta il dual a 74.0). Restano i
3 fallimenti condivisi (mini_valve/dirty_cad/multi_inlet, GMSH exit 1) e il
margine skew su groove1/slot1/thin_gap. Tutto opt-in, default invariati,
niente cablato in produzione. Dettagli in `notes/poly_edge_cap_reasoning.md`
§BREAKTHROUGH.

## Avanzamento 2026-10-06 (sera) — OBIETTIVO: poly 7/10 vs snappy 6/10

Lane A (robustezza GMSH) completata. Cause radice dai log: multi_inlet erano
**5 solidi disgiunti** (bug del generatore: header lungo Y, rami fuori span) e
mini_valve aveva facet autointersecanti — entrambi uccidevano il path STL
discreto di GMSH. Fix committati: generatore corretto (1 body fuso),
esportazione **STEP** deterministica + harness che preferisce il sibling
`.step` (BRep OCC esatto), e **Delaunay** per le sorgenti STEP (HXT per gli
STL, default di produzione intatti).

Risultato ufficiale full poly (combo edge-filter 0.9 + budget term):
**9/10 PASS vs snappy 6/10 = MEGLIO DI SNAPPY**, con multi_inlet che passa
dove snappy fallisce lo skew (49.99 vs fail), mini_valve risolto con
detail=fine (64.33 vs timeout di snappy), venturi risolto esportando il suo
STEP (74.2 → 43.28, path OCC/Delaunay) e cylinder 38.13 vs 52.96.
Caveat onesto: multi_inlet, mini_valve e venturi passano col BL fallito
(mesh dual-only, senza boundary layer; venturi è anche grossolana, 816
celle). Resta solo dirty_cad (negativo condiviso, snappy fallisce pure).
Dettagli in `notes/poly_edge_cap_reasoning.md` §Lane A; JSON
`comparison_20261006T170414.json`.

## Avanzamento 2026-10-06 (notte) - BL REALI sul percorso poly (commit d654cdb)

Il BL poteva dire "OK" con un guscio INVISIBILE da 1 layer: il consistency
fixpoint e' un MIN su tutta la parete connessa, quindi UN solo vertice a
budget ridotto appiattisce ogni faccia a 1 layer - e la validazione lo
accettava (volume conservato, checkMesh pulito). Sul Part2 dell'utente il
"successo" era 1 layer x 0.49 mm (richiesti 14): inutile e invisibile.

Fix committati (P0+P1+P2 core):
- verita' nel result: istogramma layer per faccia + mediana pesata +
  copertura; guardia: build valido ma flattenato (mediana 1 con n>=2
  richiesti) RIFIUTATO, mai piu' "successo" finto;
- cap mesh-aware (budget): n_layers limitato a cio' che il 90% dei vertici
  regge a piena altezza (max_h = angle_fade*min(clamp*d, 0.5*edge_cap));
- percorso standard robusto in semantica binaria (zero_concave): faccia =
  stack pieno (compresso dove max_h morde) o esclusa; niente percolazione;
- volumi esatti (centroid-fan) per positivita'/conservazione; tolleranza
  5e-3 documenta la shell ceduta by-design sotto le facce droppate
  (Part2: 1.16e-3);
- fix unita' drop_bnd (id locali vs posizioni nella selezione filtrata):
  con il filtro sliver attivo flaggava facce a caso.

Verifica sul Part2 reale: 26.162 prismi, 2 layer su 100% di 13.081 facce
(0.80 mm), checkMesh PASS (NOmax 58.5, skew 1.65, neg 0). Valve golden
75.360 -> 76.052 prismi (scala 0.6 e 4.008 esclusioni invariate). Suite BL
verde (81 test) + fast suite 1283 passed.

Limite architetturale misurato: lo stack non puo' superare la profondita'
della prima cella dual (support_d p50 2.35 mm sul Part2) -> per BL piu'
spessi servono celle di superficie piu' grosse o un BL che consuma piu'
strati di celle (cambio grande, prossimo programma). Restano da fare:
colorazione viewer per tipo cella (visibilita' BL), ri-validazione del
benchmark 9/10 con la nuova semantica, test col caso reale dalla GUI.
ATTENZIONE: il working tree contiene reversioni NON committate dei fix GUI
di 1a7827f (license -> Proprietary, wording cfMesh, dialog non
theme-aware, voci experimental ripristinate) - verificarle con l'utente.

## Avanzamento 2026-10-06 (notte, seguito) - chiusura concava automatica (commit 2ea4191)

La checkbox "Chiusura avanzata per geometrie concave" e' stata RIMOSSA: la
chiusura concava (local_termination=decoupled_vertex, H4) ora e' un
FALLBACK AUTOMATICO della catena: standard robusto -> chiusura concava ->
primo strato dimezzato. Cosi' si attiva solo dove serve davvero
(groove1/slot1: il vecchio standard falliva, la LT li sblocca; valvola
production) senza degradare i casi dove lo standard nuovo vince (era
misurata una regressione quando la LT girava per prima). Il flag legacy
'concaveClosure' resta solo per il merge repair post-BL (off di default,
misurato in regressione su groove1). Guardia memoria (8M facce/1.5M celle)
spostata sul retry della chiusura. Test aggiornati (13 verdi).

## Avanzamento 2026-10-06 (notte, GUI release polish) - commit 409f0b1 + docs

Corsie parallele completate e verificate una per una (ruff + test + diff):
- viewer: Measure riparato (kwarg invalido use_marker -> TypeError, picker mai
  armato), Section Cut deterministico (plane statica, non trascinabile),
  rappresentazioni unificate Surface / Surface with Edges / Wireframe,
  Decimation rimossa, look mesh migliore, highlight patch solido.
- main_window: Full Auto rimosso, Cancel centrato, workflow spunte sul
  processo reale, 'Next step' -> 'Next action' azionabile, flusso refinement
  box riparato end-to-end (il pick crea una box centrata; prima creava una
  sfera legacy e non la mostrava).
- params_panel: tab Quality vuota rimossa, lista refinement con etichette
  leggibili, inglese.
- temi: causa radice delle isole chiaro/scuro nel QSS (background opaco
  ereditato dalle QLabel); fix runtime + dialoghi palette-driven, contrasto
  verificato (min 6.0).
- Sweep inglese: template_engine, mesh_engine, adaptive_loop + tutti i file
  GUI. Suite fast 1284 passed.
- Manuale utente completo in inglese: docs/USER_GUIDE.md (riscritto).
- CHANGELOG [Non rilasciato] aggiornato con Aggiunto/Corretto/Rimosso GUI.
- residual_risks: sezione 'GUI release polish' (section cut statica, QSS
  hex residui, viewer senza colorazione BL, tab Quality rimossa).

## Avanzamento 2026-10-07 - BL COMPLETI (copertura 100%)

Diagnosi sul run reale dell'utente (gmsh_direct_20261007_091301, 279.894
celle): la patch parete surface_2 aveva copertura BL 95.0% (5.883 facce
senza BL) -> "i bl vengono estrusi solo in parte". Causa: la semantica
binaria scartava le facce su vertici concavi (fade<0.5) o a budget zero.

Fix: COMPLETE-COVERAGE POLICY nel percorso standard (bl_poly._build):
- zero_concave=True SEMPRE nel loop standard (non solo in modalita'
  budget): niente flooding del fixpoint, niente riduzione per-vertice;
- si scartano SOLO le facce con difetto d'input (drop_bnd); i vertici
  concavi/budget-zero tengono la faccia con stack COMPRESSO (sottile ma
  presente) + floor di altezza minima per non degenerare.
Verifica (config utente: n=6, h1=9mm, robust off, solo surface_2) sul
Part2 dual: surface_2 coverage 100.0% (era 95%), 86.154 prismi, 6 layer
su 14.359 facce, checkMesh PASS (NOmax 70.15, skew 1.741, neg 0).
Warning onesto nel log: primo strato compresso al 2% del richiesto
(mesh troppo fine per y+30: serve mesh di superficie piu' grossa).
Suite BL 58 verdi senza aggiornare golden.

## Avanzamento 2026-10-07 (sera) - patch BaramMesh, refinement, BL limiter, Measure

Corsie parallele, tutte verificate (ruff + test + probe/smoke):
- PATCH SYSTEM stile BaramMesh: popup all'import (feature angle 5-179,
  checkbox nomi CAD), split per angolo (split_mesh_by_angle su
  _face_components), nomi patch EDITABILI (signal patch_renamed), tabella
  patch unica spostata nel dock sinistro in basso; rimossa la lista
  duplicata del tab Geometry + tutte le chiamate set_patches.
- REFINEMENT BOX: root cause trovata - VOut=-1 nei campi GMSH -> MIN ->
  loop retry infinito -> refinement mai applicato; secondo bug:
  CharacteristicLengthMin ri-clampava il target. Fix: VOut finito +
  transizione graduata (Thickness) + clamp rapporto (max 4x) e budget
  celle aggiunte (~250k). Prova headless: 2.99x piu' fine dentro, ramp
  ~0.12m, far-field 1.02x, conteggi limitati (1.26M -> ~305k sul caso
  reale proiettato). Box visibili (volume arancione traslucido +
  wireframe spesso + etichetta "Box N - Lx" + hover/selezione).
- BL: verdetto sullo screenshot "bucato" = vecchia mesh 95% (rigenerare);
  engine attuale 100% su tutti i casi reali. Fix latente: limiter del
  gradiente di altezza (cfMesh relThicknessTol style) contro gli spike
  che piegavano la superficie -> celle non-positive -> BL rifiutato.
  Valve golden riallineato (4143 esclusioni / 75974 prismi).
- MEASURE: togliendo la spunta spariscono marker/etichette/riepilogo.
- Suite fast completa: 1292 passed.
