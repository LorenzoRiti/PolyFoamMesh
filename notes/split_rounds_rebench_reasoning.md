# Split-rounds re-measurement on the regenerated valve fixture (FASE 6 follow-up)

Data: 2026-09-09. Scritto PRIMA della misura. Le voci `docs/residual_risks.md`
§Polyhedral Conversion e `docs/dev/handoff_poly_bl_deepseek.md`
(line 93) dicono: "split_rounds remains disabled: measured to add cost
without reducing defects on the reference part. Re-measure before
enabling." La misura originale (352 vertici concavi non-migliorano)
risale al converter pre-FASE 7 (pre-smoothing-default,
pre-`_solve_global_windings`, pre-fixture-rigenerata). Il converter
attuale potrebbe dare un risultato diverso: bug-fix, smoothing di
default, e winding solver globale hanno cambiato il panorama.

## Misura

- `TetPolyDualConverter(case, split_rounds=N)` con N=0 (baseline) e
  N=1 (split). Tutto il resto invariato (default del converter sul
  tet backup della valvola in `C:/polybench/valve1`).
- Output per run: `res.residual_defects`, `res.defect_breakdown`
  (pyramid / non_ortho / skew), `res.volume_before/_after/_drift`,
  `res.time_s` (tempo della conversione).
- Confronto A/B sui tre numeri, docstring current converter.

## Cosa mi aspetto

Il commento storico ("adds ~20s per round for nothing") fu misurato
sul converter pre-FASE 7. Predico che la sostanza NON cambia: il
keep-best del converter seleziona il round col minor conteggio di
difetti, e se nessun round riduce i difetti il "miglior" è il primo
(round 0), e i round successivi sono scartati. Cioe' in totale il
risultato finale sara' equivalente a split_rounds=0.

Predico (numeri attesi sul fixture rigenerato, modalita' default):
- split_rounds=0: residual_defects ~X (la fixture e' "fresh" 863/9/14.58
  vs il vecchio 895/55/44.3; la differenza del converter recente
  dovrebbe essere misurabile ma non eclatante);
- split_rounds=1: residual_defects <= X (non-migliora nella storia
  documentata; lo split del vertex star rompe la c in piu' piccole
  che a loro volta violano il pyramid test, keep-best scarta);
- tempo: N=1 dovra' essere piu' alto di N=0 (almeno un round
  in piu'); la differenza sara' dell'ordine di decine di secondi
  (piccola, su un converter di ~100s).

Onestamente: 70% che la misura conferma lo status quo; 30% che
qualcosa e' cambiato (es. il smoothing di default e il winding solver
globale hanno cambiato il panorama e la split adesso potrebbe
aiutare, oppure creare difetti dove prima non ne creava). In entrambi
i casi il risultato sara' onesto.

## Criterio di lettura

- Se i numeri confermano (split_rounds non aiuta): documento onesto,
  nessun cambiamento al default (split_rounds=0 resta off).
- Se cambiano (split_rounds ora aiuta, oppure crea difetti dove
  prima non ne creava): documento il finding, propongo l'aggiornamento
  della nota in `docs/residual_risks.md` §Polyhedral Conversion, e in
  un commit separato (se l'utente conferma) l'eventuale
  abilitazione. Per questa sessione: solo misura + docs, **nessun
  default cambiato**.

Il working dir per la misura e' `C:/polybench2/split_rounds_rebench/`
(per specchiarsi col pattern FASE 7/H4).
