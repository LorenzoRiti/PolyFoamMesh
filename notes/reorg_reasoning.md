# REORG — reasoning e previsioni per blocco

Date: 2026-10-23. Predecessor: `notes/reorg_diagnosis.md`.
Previsioni scritte PRIMA di eseguire i blocchi.

## Blocco A (audit + rimozioni provate)

Previsione: l'audit non troverà alcun modulo rimovibile — ogni
candidato del prompt ha importatori/test/dispatch vivi (§2 della
diagnosi). Risultato atteso: **zero righe di codice toccate**,
record dell'audit in `residual_risks.md`, suite e ruff verdi per
assenza di modifiche (non per merito). Falsificazione: se emerge un
modulo senza importatori né test né entry-point, viene rimosso con
commit separato + gate completo — ma la diagnosi dice che non
accadrà, perché i sospetti noti (commercial/*, hex_poly_dual,
validate_bl_params, tools/) sono già stati tutti verificati vivi.

## Blocco B (consolidamento duplicati)

Previsione: i tre set candidati risultano non-duplicati a ispezione
ravvicinata (centroidi diversi per definizione; ruoli diversi;
chiamante legacy vivo). Risultato atteso: **zero consolidamenti**,
con la chiusura esplicita del debito "duplicati _face_geometry" come
decisione documentata (non residuo trascinato). Falsificazione:
equivalenza numerica dimostrata su fixture (hash identico) → allora
sì, unifica con alias + test. Non prevista: le definizioni differiscono.

## Blocco C (riorganizzazione layout)

Previsione: NON giustificato, e va detto forte. Il layout attuale ha
assorbito W13 (registry + adapter + worker + 2 file di test) senza
attriti né bug da struttura; nessun difetto noto è attribuibile al
layout; spostare 15+ file riscrive import/packaging/CI e invalida i
riferimenti storici delle note, con guadagno zero misurato. Per §8
si consegna A+B e ci si ferma qui — esplicitamente, non per omissione.

## Gate e commit

- Gate per blocco: sottoinsieme mirato della suite che tocca le aree
  ispezionate (config/preset/registry/BL/dual/gui-mesher) + `ruff`.
  Full suite solo se un blocco tocca codice (non previsto).
- Commit: uno per deliverable reale (docs audit + record). Nessun
  commit cosmetico per "mostrare lavoro".
