# W18 — reasoning e previsioni

Date: 2026-10-24. Predecessore: `notes/w18_auto_polymesh_diagnosis.md`.
Previsioni scritte PRIMA di edit/build/test. La tabella vincitori in
diagnosi §3 è la base; niente "dovrebbe".

## P1 — policy auto (choose_polymesh_engine)

Predizione: mapping deterministico feature→(primary, f1, f2, reason):
open/non-watertight→fTetWild; no-pytetwild→GMSH; default GMSH con
fallback fTetWild; dirty→fTetWild. bl_on_tets MAI in auto. Test
puramente in-memory (preflight finti) verdi al primo colpo perché la
policy è una tabella, non un euristica. Falsificazione: un caso della
tabella §3 che la policy assegna al perdente misurato → correggere la
tabella, non forzare il caso (keep-best vale anche per la policy).

## P2 — UI due CTA, no combo

Predizione: `Polymesh` (key `auto`, checked default) + `cfMesh` visibili;
combo rimosso da main+Advanced; bottoni nascosti intatti; preset/progetti
espliciti migrano e restano su key salvata. `test_mesher_registry.py`
riscritto (2 CTA, no combo, migrazione) verde; suite esistente verde a
parte i test pinnati al vecchio layout (solo quelli si toccano).
Falsificazione: dispatch che ignora la key salvata (test migrazione lo
trova) o cfMesh che non parte (smoke WSL/OF lo dice subito).

## P3 — fallback reale + provenienza

Predizione: primary invalido → f1 → f2 senza input utente, ogni tentativo
loggato con reason; catena esaurita = errore con catena nominata.
Provenienza riusa `TetSourceResult.to_mapping` + plan reason (test
assertano i campi, non il testo libero). Falsificazione: fallback che
sovrascrive un esito valido (il test keep-best con runner finti lo trova:
vince il primo valido, mai sostituito).

## P4 — tabella §3.4 (auto vs espliciti, 8 casi)

Predizione: auto vince/pareggia su 6/8 (tutti tranne box_obstacle e
external_aero, dove NESSUN esplicito è verde — lì auto pareggia il
migliore misurato per costruzione, dichiarato). R1: l'auto (fTetWild
tet → dual + split come wall03, oppure direct-BL?) MISURA pyramids/skew/
Failed/coverage; se il criterio operativo (pyr=0, skew-faces≤1,
Failed≤1, cov≥98%) non è raggiunto, NESSUNA promozione: si dichiara e
resta default=auto-comunque? NO — se R1 fallisce, la policy resta ma il
verdetto lane è NOT MET sul criterio R1 (onesto, non aggiustato).
Falsificazione: flake fTetWild (~1/5) — retry codificato, tabella
riporta tentativi come wall03.

## P5 — gate e commit

Suite fast + ruff verdi; commit locale unico. File stimati 10-12.
`benchmarks/run_comparison.py` + `tests/test_run_comparison.py` mai
staged (modifiche utente). Niente push.
