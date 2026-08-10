# CONTRIBUTO — chiusura mesher poliedrico + boundary layer (FASE 0 + FASE 1)

> Compilato da Reasonix, 2026-08-10. Blocco obbligatorio §10 del megaprompt.
> I numeri qui sotto sono tutti misurati (checkMesh WSL reale, engine in-process,
> solver WSL), mai assunti.

```python
CONTRIBUTO = {
    "autore": "Reasonix (2026-08-10)",

    "comprensione": """
    Il percorso di produzione è: STL/STEP -> GMSH tet (Windows nativo) ->
    gmshToFoam -> TetPolyDualConverter (dual baricentrico/mediano, 100% poly,
    bordo = suddivisione esatta dei triangoli in 3 quad planari) ->
    PolyBoundaryLayerEngine (prismi di parete, puro numpy) -> checkMesh.
    Il difetto residuo della valvola: dove un vertice primale di bordo sta su
    uno spigolo di feature CONCAVO del CAD, la cella duale avvolge l'angolo ed
    è genuinamente non convessa -> il centroide cade fuori da una sua quad di
    bordo -> fallisce il check 'face pyramids' di checkMesh (852 facce, 0.25%
    delle celle). Le celle sono chiuse, a volume positivo, watertight: fallisce
    un check che ASSUME convessità. Le strade a livello di convertitore
    (agglomerazione, split segnato/non segnato, wedge cells, median_faces)
    sono chiuse con numeri (§3 megaprompt). Il mesher nativo cartesiano
    (hex_poly_dual) è un ramo sperimentale separato (menu Tools), non il
    percorso di produzione qui trattato. Il mio lavoro copre FASE 0 (decisione
    sul difetto) e FASE 1 (BL selettivo per patch); FASE 2/3 e il collasso
    delle facce di bordo (collapse_smooth_edges) sono dell'agente parallelo.
    """,

    "fase_0_esito": """
    potentialFoam sulla valvola (stessa geometria, stesse BC, tet 824.661 vs
    poly 152.086):
      - entrambi convergono (End, nessun FATAL);
      - residuo finale GAMG su Phi: tet 3.07e-5 (3 it), poly 4.60e-6 (2 it);
      - Continuity error: tet 8.71e-3 (18% del flusso), poly 7.83e-4 (1.6%);
      - volume: 0.121835 vs 0.121835 (0.00%);
      - flusso prescritto all'inlet: 0.0482551 vs 0.0482551 (0.00%).
    DECISIONE: G3 NON è un bug — è un limite documentato di un check che
    assume convessità su celle che il solver digerisce perfettamente. La poly
    della valvola risolve, converge e conserva la portata MEGLIO del tet da
    cui deriva. FASE 3 (taglio planare) NON va fatta; stop rule rispettata
    perché la premessa è falsa. Report completo: docs/poly_solver_validation.md.
    """,

    "cosa_ho_implementato": """
    FASE 1 (BL selettivo per patch, chiude G1):
    - src/cfmesh_autogui/core/bl_poly.py: _select_faces non auto-chiude più la
      selezione; al bordo BL/non-BL le facce laterali dei prismi giacciono NEL
      PIANO della parete adiacente e diventano facce di BOUNDARY della patch
      senza BL, possedute dai prismi (nFaces della patch non-BL cresce,
      startFace ricalcolato); le facce di parete non selezionate muovono i
      vertici di parete condivisi all'ultimo layer (senza questo la cella
      vicina resta aperta: misurato 8 celle non chiuse, max rel 4.43e-3, con
      la costruzione a facce interne della bozza parallela). Numeri prima/dopo
      sul cubo con patch wall+inlet: prima il BL parziale falliva su TUTTE le
      scale (validazione: celle non chiuse); dopo successo a scale=1.0,
      volume conservato, 12 celle prisma (2 layer x 6 facce), 16 facce di
      terminazione.
    - Verifica reale checkMesh (tools/bench_bl_poly_partial.py), 2 geometrie:
        A) cilindro con patch nominate inlet/outlet/wall: BL su wall, 72
           prismi = 3 layer x 24 facce di parete, 48 terminazioni,
           checkMesh MESH OK;
        B) cubo duct idem: 81 celle (72 prisma + 9 poly), skew 2.18,
           non-ortho max 44.8, checkMesh MESH OK.
    - src/cfmesh_autogui/core/openfoam_runner.py: apply_to_all hardcoded
      rimosso; default = solo patch wall (type 'wall' o nome *wall*), fallback
      geometrico poly_geo_wall_patch_names (infer_patch_roles) per casi GMSH
      grezzi, fallback finale apply_to_all con warning; applyToAll dalla GUI.
    - src/cfmesh_autogui/gui/params_panel.py: get_bl_params() ora porta
      applyToAll (checkbox prima morta sul percorso poly).
    - tools/poly_solver_validation.py: parametrizzato per caso (ref1/valve1)
      e variante (default/production), log completo, parse residui/iterazioni/
      continuity.
    - tools/bench_bl_poly_partial.py: nuovo (gate FASE 1 con checkMesh reale).
    - tests/: test_bl_poly.py (terminatori come boundary faces, conteggi
      derivati dal mesh), test_openfoam_runner.py (poly_wall_patch_names +
      poly_geo_wall_patch_names), test_bl_gui_wiring.py (applyToAll).
    """,

    "proposta_personale": """
    La verifica FASE 0 ha mostrato che la poly della valvola conserva la
    portata 11 volte meglio del tet (continuity 7.8e-4 vs 8.7e-3). Proposta
    misurabile: aggiungere alla pipeline un METADATO di qualità 'solver-ready'
    (convergenza potentialFoam + continuity error) esposto nel report del
    worker poly, invece del solo checkMesh-verde. Oggi la GUI giudica 'ok' con
    checkMesh, che sulla valvola dice FAIL anche se il solver digerisce la mesh
    benissimo. L'esperimento che la verificherebbe: eseguire il percorso GUI
    completo sulla valvola e confrontare il verdetto checkMesh (Failed 3) con
    il verdetto potenziale (converge, 1.6%) — il gap è la misura di quanto
    l'UX stia scoraggiando l'utente da mesh utili.
    """,

    "limiti_residui": """
    - FASE 2 (terminazione locale dei layer n->n-1->...->0) NON fatta in
      questa sessione (è dell'agente parallelo / lavoro futuro). Conseguenza
      misurata: sulla valvola (difetti concavi pre-esistenti) il BL fallisce
      pulito su tutte le scale e la mesh resta senza layer — il fallback
      globale su 5 scale azzera il BL ovunque per colpa di una zona.
    - FASE 3 non fatta: la FASE 0 ha mostrato che non serve (limite
      documentato).
    - Il test lento test_valve_fixture_bl_invariants (BL full sulla fixture
      valvola 1.05M punti) impiega decine di minuti; la suite rapida lo
      esclude. Il percorso full-BL non è cambiato (output identico con
      apply_to_all=True).
    - poly_wall_patch_names non usa infer_patch_type di meshdict_gen perché
      quella funzione (semantica cfMesh) classifica QUALSIASI nome sconosciuto
      come wall — inadatta al default BL; il criterio esplicito (type wall /
      nome *wall*) + fallback geometrico è la semantica corretta.
    """,

    "regressioni": """
    Verdi: tests/test_bl_poly.py (5 veloci), tests/test_bl_gui_wiring.py (6),
    tests/test_openfoam_runner.py (6 su 7; test_mesh_worker escluso: lancia
    cartesianMesh WSL, minuti, pre-esistente), tests/test_tet_poly_dual.py +
    tests/test_poly_smoother.py (17).
    Suite completa offline: 926 passed, 1 skipped; 4 FAILED tutti
    pre-esistenti/ambientali, nessuno nel territorio FASE 0/FASE 1:
      - test_native_mesher.py::test_cube_exact_volume_and_counts: fallisce
        IDENTICAMENTE su HEAD~1 (0.97559375 vs 1.0 — ramo cartesiano
        sperimentale, non toccato);
      - test_watertight_meshdict::...do_not_need_a_qt_event_loop: flake
        ordine-sensitivo documentato in residual_risks.md, passa in isolamento;
      - test_baramflow_roundtrip::test_exported_case_loads_in_openfoam e
        test_e2e_workflow::test_full_workflow: richiedono WSL + cartesianMesh
        esterno (ambientali, fuori dal percorso poly+BL).
    Baseline valvola: tools/valve_defect_baseline.py --conv-only = PASS
    (1027 difetti residui, 852 piramidi, volume conservato) — invariata.
    Test lento test_valve_fixture_bl_invariants (full-BL sulla fixture valvola
    1.05M punti): non completato in sessione (>75 min CPU; percorso full-BL
    equivalente al precedente, limite G2 documentato).
    Nessuna regressione nel territorio FASE 0/FASE 1.
    """,
}
```

## File toccati (FASE 0 + FASE 1)

- `src/cfmesh_autogui/core/bl_poly.py` — terminazione a boundary faces + vertici mossi
- `src/cfmesh_autogui/core/openfoam_runner.py` — default wall-only + fallback geometrico
- `src/cfmesh_autogui/gui/params_panel.py` — applyToAll nel percorso poly
- `tools/poly_solver_validation.py` — FASE 0 (valvola)
- `tools/bench_bl_poly_partial.py` — gate FASE 1 (nuovo)
- `tests/test_bl_poly.py`, `tests/test_openfoam_runner.py`, `tests/test_bl_gui_wiring.py`
- `docs/poly_solver_validation.md` (nuovo), `docs/residual_risks.md`
