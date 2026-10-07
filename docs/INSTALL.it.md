# Installazione

[English](INSTALL.md) | **Italiano**

Due percorsi: **installer precompilato** (consigliato, non serve Python) o
**da sorgente** (per sviluppatori/contributori).

## Opzione A — Installer Windows (consigliata)

### Requisiti
- Windows 10 o 11, 64 bit
- ~2 GB di RAM libera (consigliati 4+)
- ~2.5 GB di spazio su disco
- Non serve Python, non serve nient'altro

### Passi
1. Scarica `PolyFoamMesh-<versione>-Setup.exe` dalla pagina
   [Releases](https://github.com/LorenzoRiti/PolyFoamMesh/releases) del repository.
2. Doppio clic sull'installer.
3. Se Windows SmartScreen avvisa ("Windows ha protetto il PC"): l'exe non è
   firmato con un certificato a pagamento — è un falso positivo comune per
   software distribuito da sviluppatori indipendenti.
   **"Ulteriori informazioni" → "Esegui comunque"**.
4. Completa l'installazione (disponibile in italiano). Al termine l'app si
   avvia da sola.
5. Da quel momento: **Start → PolyFoamMesh** o l'icona sul desktop.

### Cosa funziona subito (senza altro da installare)
| Percorso | Dove | Risultato |
|---|---|---|
| CFD Poly (GMSH, no-WSL) | Mesh tab | Tet GMSH → dual → mesh 100% poliedrica |
| FEM Tetra (GMSH, no-WSL) | Mesh tab | Mesh tetraedrica per solver FEM |
| Visualizzazione | Viewer 3D | Geometria e mesh |

### Cosa richiede un passo in più
- **Cartesian mesh (cfMesh)** e **validazione checkMesh** richiedono
  OpenFOAM in WSL2 (vedi Opzione C sotto). Senza, l'app mostra un avviso
  chiaro e non procede su quei percorsi — non è un errore, è il
  comportamento atteso finché non installi OpenFOAM.

### Provalo in 2 minuti
1. **File → Load Geometry...** → apri uno STL chiuso/watertight (es. un
   modello di test incluso).
2. Mesh tab → **"CFD Poly (GMSH, no-WSL)"**.
3. Case directory: un percorso **senza spazi** (es. `C:\prova`).
4. **Run** → nel log vedi GMSH generare il tet, poi il dual poliedrico.

### Limiti noti (onesti)
- STL auto-intersecanti (triangoli sovrapposti, fori non tagliati nel CAD)
  possono far fallire GMSH con `PLC Error: two segments intersect` — usa STL
  esportati puliti.
- Formati consigliati: **STL chiuso** (watertight) o **STEP**.
- Vedi [residual_risks.md](residual_risks.md) per i limiti tecnici noti
  dell'engine di meshing.

### Disinstallazione
**Start → PolyFoamMesh → Disinstalla**, o Impostazioni → App →
PolyFoamMesh → Disinstalla. I log restano in
`%APPDATA%\polyfoammesh\logs` (cancellabili a mano).

## Opzione B — Da sorgente (sviluppatori)

```bash
git clone https://github.com/LorenzoRiti/PolyFoamMesh.git
cd PolyFoamMesh
pip install -e ".[test]"
polyfoammesh
```

Requisiti: Python 3.11+. Se tocchi il codice, installa una volta
`python -m pre_commit install`: a ogni commit girano ruff (regole bug) e il
sottoinsieme veloce di test puri.

## Opzione C — Aggiungere OpenFOAM/cfMesh (opzionale, entrambi i percorsi)

Serve solo per i percorsi Cartesian cfMesh e checkMesh.

```powershell
wsl --install -d Ubuntu
# dentro Ubuntu:
sudo apt-get update
sudo apt-get install openfoam2512
```

Oppure usa lo script incluso `installer/setup_wsl_openfoam.ps1` (richiede
privilegi admin, ~1-2 GB di download). Dopo il setup, tutti i percorsi
(cfMesh, checkMesh) funzionano senza altre modifiche.

## Opzione D — Linux (da sorgente)

PolyFoamMesh gira nativamente su Linux. I percorsi puramente GMSH (CFD Poly,
FEM Tetra, viewer) non richiedono OpenFOAM; i percorsi Cartesian cfMesh e
checkMesh richiedono OpenFOAM nel `PATH`.

### Installare l'app

```bash
git clone https://github.com/LorenzoRiti/PolyFoamMesh.git
cd PolyFoamMesh
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
./launch.sh          # oppure: bash launch.sh
```

`launch.sh` e' l'equivalente Linux di `launch.ps1`: prova ogni interprete
candidato per PySide6 + pyvista e usa il primo che funziona. In alternativa
`polyfoammesh` (lo script console installato da pip) avvia l'app
direttamente. Su un sistema minimale servono le solite librerie Qt/X11
(Debian/Ubuntu: `sudo apt-get install libegl1 libgl1 libglu1-mesa
libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libfontconfig1`).

### Installare OpenFOAM (solo per cfMesh / checkMesh)

Package della distribuzione oppure build OpenFOAM Foundation:

```bash
# Opzione 1 — repo apt OpenFOAM Foundation (layout di default)
#   env script: /usr/lib/openfoam/openfoam2512/etc/bashrc
# Opzione 2 — build da sorgente / manuale
#   env script: /opt/openfoam2512/etc/bashrc
```

Assicurati che `cartesianMesh`, `checkMesh` e `foamToVTK` siano nel `PATH`
della shell che avvia PolyFoamMesh — normalmente sorgendo l'`etc/bashrc` di
OpenFOAM in `~/.bashrc`. Se l'env script e' in una posizione non standard,
indicalo esplicitamente:

```bash
export POLYFOAMMESH_OF_BASHRC=/opt/openfoam2512/etc/bashrc
./launch.sh
```

Su Linux non c'e' il passaggio WSL: ogni comando OpenFOAM viene eseguito
come semplice `bash -lc`. I binari `cfMesh`, `checkMesh` e `foamToVTK`
devono quindi essere risolvibili da `bash`.

### Dove stanno i dati

Su Linux l'app salva sessioni e log nella directory XDG:
`$XDG_DATA_HOME/polyfoammesh` (default `~/.local/share/polyfoammesh`), con i
log in `logs/app.log`. Su Windows nulla cambia (`%APPDATA%\polyfoammesh`).

## Problemi?

Apri una issue con il contenuto di `%APPDATA%\polyfoammesh\logs\app.log` —
vedi [CONTRIBUTING.md](../CONTRIBUTING.md).
