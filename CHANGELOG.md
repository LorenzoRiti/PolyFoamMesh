# Changelog

Tutte le modifiche notevoli a PolyFoamMesh sono documentate qui.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e
il progetto segue [Semantic Versioning](https://semver.org/lang/it/).

## [2.2.0] - 2026-09-08

### Cambi di rottura (breaking)

- **Percorso dati rinominato**: i dati applicativi sono passati da
  `%APPDATA%\cfmesh-autogui\` a `%APPDATA%\polyfoammesh\` (log, sessioni,
  telemetria octopoda). Alla prima esecuzione la nuova cartella viene
  popolata automaticamente copiando il contenuto della vecchia, se
  presente. Chi ha installato una versione precedente non perde nulla, ma
  il vecchio percorso non viene più scritto. Le impostazioni applicative
  (tema, parametri recenti) vivono nel registro di Windows e non cambiano
  posizione: restano dove erano, così le preferenze dell'utente sopravvivono
  al rename del pacchetto (`cfmesh_autogui` → `polyfoammesh`).

### Aggiunto

- **Pipeline di release**: su push di un tag `v*`, GitHub Actions builda
  l'eseguibile PyInstaller + l'installer Inno Setup su Windows e li allega
  alla GitHub Release del tag (con changelog estratto da questo file).
  Rimossi gli script installer obsoleti (NSIS e Inno duplicati); corretto
  `UninstallDisplayName` in `inno_setup.iss` (direttiva spezzata su due
  righe).
- **Avviso di sostituzione algoritmo nella GUI**: quando il motore sostituisce
  silenziosamente un percorso di meshing (boundary layer disattivato,
  parallelo→seriale, retry GMSH più grossolano, retry di qualità), ora il
  log panel mostra una riga `[SUBSTITUTED]` evidenziata — mai più silenzio.
- **Documentazione bilingue**: `docs/INSTALL.md` e `docs/USER_GUIDE.md`
  riscritte in inglese (principali), versioni italiane in
  `docs/INSTALL.it.md` / `docs/USER_GUIDE.it.md`, link incrociati in cima.
- **Community**: `CODE_OF_CONDUCT.md`, template PR in
  `.github/PULL_REQUEST_TEMPLATE.md`, 5 issue "good first issue" sul repo
  pubblico.
- `_paths.py`: fonte unica per la directory dati applicativi e migrazione
  one-shot dal percorso legacy.

## [2.1.0] - 2026-09-08

### Cambi di rottura (breaking)

- **Rename del pacchetto Python**: `cfmesh_autogui` → `polyfoammesh`. Gli
  import nel codice, nei test e nei tool sono stati aggiornati; i moduli
  entry point della CLI sono ora `polyfoammesh`, `polyfoammesh-mesh` e
  `polyfoammesh-batch`. L'eseguibile PyInstaller si chiama `PolyFoamMesh`.

### Aggiunto

- CI riportata verde su GitHub Actions (era rossa su ogni push): `pytest-qt`
  negli extra, percorsi compatibili POSIX in `config.py`/`validation.py`,
  dipendenze `networkx` e `rtree` dichiarate.

### Corretto

- Zone di raffinamento manuali: `setAsBackgroundMesh` scartava il campo
  adattivo geometrico; ora MIN-combinato (`gmsh_wrapper.py`).
- Corpi curvi chiusi (es. sfera) tessellati non-watertight: vertici
  coincidenti a cuciture/poli ora fusi e triangoli degeneri scartati
  (`geometry.py::tessellate_patches`).
- Test che sovrascrivevano `sample_cad/` tracciato; stub sempre-skip
  sostituito con un test di integrazione reale.
- Storia git: blob da ~278 MB (`installer/output/*.exe`) rimosso dalla
  storia di `master` con `filter-branch`.

### Nota

- L'eseguibile e l'installer precompilati non vengono committati nel repo:
  si trovano rispettivamente in `dist/` e `installer/output/` (entrambi
  ignorati da `.gitignore`) e vengono generati localmente o dalla pipeline
  di release.

[2.2.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.2.0
[2.1.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.1.0
