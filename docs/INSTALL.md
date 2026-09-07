# Installazione

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
   [Releases](https://github.com/OWNER/cfmesh-autogui/releases) del repository.
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
`%APPDATA%\cfmesh-autogui\logs` (cancellabili a mano).

## Opzione B — Da sorgente (sviluppatori)

```bash
git clone https://github.com/OWNER/cfmesh-autogui.git
cd cfmesh-autogui
pip install -e ".[test]"
pip install gmsh meshio reportlab
python -m polyfoammesh.app
```

Requisiti: Python 3.11+.

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

## Problemi?

Apri una issue con il contenuto di `%APPDATA%\cfmesh-autogui\logs\app.log` —
vedi [CONTRIBUTING.md](../CONTRIBUTING.md).
