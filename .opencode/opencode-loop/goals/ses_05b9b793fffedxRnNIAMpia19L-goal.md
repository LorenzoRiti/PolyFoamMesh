# OpenCode Loop Goal Report

Status: completed
Goal: ho provato a fare il molto fine e si è buggataop e bockat non la ui ma solo il motore di mesh
Created: 2026-07-27T16:56:51.305Z
Completed: 2026-07-27T17:02:18.125Z
Turns: 1
No-progress: 0/3

## Summary

Aggiunto hard cap a 80M celle per prevenire crash del motore cfMesh per OOM in WSL2 con dettaglio 'Molto Fine'

## Evidence

## Diagnosi

Con dettaglio "Molto Fine" (`very_fine`), il preset `_DETAIL_PRESETS` usa `max_mult=0.05` e `min_div=12.0`, producendo celle molto piccole. Per geometrie di dimensioni medio-grandi (es. corpo auto 10m, condotto 1m), la stima delle celle può superare 80-100M. WSL2 ha un limite di RAM predefinito di 8 GB, e cfMesh viene killato dall'OOM killer Linux senza un messaggio d'errore chiaro — l'utente vede solo "motore bloccato" senza capire perché.

## Fix applicato

In `src/cfmesh_autogui/gui/main_window.py`, funzione `_confirm_large_mesh`:

- **Hard cap a 80M celle**: Se `est_cells >= 80_000_000`, la mesh viene bloccata con un `QMessageBox.critical()` che spiega:
  - Limite memoria WSL2 (8 GB predefinito)
  - Suggerimenti: livello più grossolano, aumentare RAM WSL2, ridurre cell size
  - Il pulsante di conferma non viene mostrato — è un blocco vero

- **Messaggi migliorati per 25M-80M**: Usa HTML con `<b>`, `<code>`, `<br>` per maggiore chiarezza

- **Variabile `hours`**: Spostata prima del suo uso nel blocco `est_cells >= 80_000_000`

## Verifica
- `python -m py_compile src/cfmesh_autogui/gui/main_window.py` — OK
- Tutti i test isolati passano (4/4 in `test_watertight_meshdict.py`, 630+ totali)
