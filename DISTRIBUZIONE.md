# Come distribuire CFMesh-AutoGUI (v2.1.0)

Guida per lo sviluppatore. Per l'amico c'è il PDF
`installer/output/CFMesh-AutoGUI-2.1.0-Istruzioni.pdf` (o `INSTALL_AMICO.md`).

## Il pacchetto (scelta: demo no-WSL)

L'installer **non** include WSL2/OpenFOAM: l'amico prova subito i percorsi
**no-WSL** (CFD Poly GMSH → 100% poly stile STAR-CCM+, FEM Tetra) e la
visualizzazione. I percorsi WSL (cfMesh, checkMesh) mostrano un avviso
chiaro e non procedono.

```
installer/output/
├── CFMesh-AutoGUI-2.1.0-Setup.exe      ← DA DARE ALL'AMICO (279 MB, installer Inno)
└── CFMesh-AutoGUI-2.1.0-Istruzioni.pdf ← istruzioni in PDF
```

## 1. Ricostruire il bundle one-dir (lato tuo, una volta sola)

```bash
python -m PyInstaller --noconfirm --clean CFMesh-AutoGUI.spec
```

Risultato: `dist/CFMesh-AutoGUI/` (1.3 GB) — app self-contained
(Python + PySide6 + cadquery + trimesh + pyvista + gmsh + pymeshfix):
**nessuna dipendenza esterna sul PC dell'amico**.

Punti critici dello spec (verificati oggi):
- **one-dir** (EXE bootloader + `_internal/`): avvio veloce, niente
  estrazione in temp a ogni lancio, più gentile con SmartScreen.
- **`gmsh-4.15.dll` aggiunta ai datas**: il wheel gmsh la installa in
  `Python311\Lib\` (fuori dai package) → PyInstaller non la vede da solo;
  senza, il percorso GMSH nel frozen crasha con "DLL load failed".
- **`gmsh_wrapper._cli_main(argv)`**: nel frozen exe il subprocess GMSH
  parte con `--gmsh-volume` su `sys.executable`; `app.py` chiama
  `_cli_main` direttamente (il vecchio `runpy.run_path` su un file che
  non esiste nel bundle falliva).
- `excludes` con torch/cv2/pyarrow/... (roba estranea dall'ambiente
  globale) — se l'exe si gonfia, controlla lì.

## 2. Compilare l'installer Inno

Inno Setup 6.7.3 portabile in `tools/innosetup/is6/ISCC.exe`
(installato con `/PORTABLE=1`, niente admin).

```bash
tools/innosetup/is6/ISCC.exe installer\inno_setup.iss
```

Risultato: `installer/output/CFMesh-AutoGUI-2.1.0-Setup.exe` (279 MB,
lzma2/max). Proprietà: installazione **per-user senza admin**
(`PrivilegesRequired=lowest`), shortcut Start Menu+Desktop, associazioni
`.step/.stp/.stl` (HKCU), disinstaller pulito.

> **Attenzione Git-Bash**: gli switch `/VERYSILENT` vengono manglati in
> percorsi MSYS (`C:/Program Files/Git/VERYSILENT`). Usare:
> `MSYS2_ARG_CONV_EXCL='*'` (o lanciare da cmd/PowerShell).

## 3. Test rapido dell'installer

```bash
# installazione silenziosa (path senza spazi)
MSYS2_ARG_CONV_EXCL='*' ./installer/output/CFMesh-AutoGUI-2.1.0-Setup.exe \
    /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=C:\polybench\install_test" /NOICONS
# verifica percorso GMSH dall'exe installato
"C:/polybench/install_test/CFMesh-AutoGUI.exe" --gmsh-volume \
    --step=C:/cfmesh_poly_bench/venturi.stl --msh=C:/polybench/v.msh \
    --detail=coarse --max-cell=0.05 --min-cell=0.02   # → {"success": true}
# disinstallazione (nella cartella di test)
"C:/polybench/install_test/unins/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

## 4. SmartScreen / firma

L'exe **non è firmato** (certificato a pagamento ~100 €/anno). L'amico
dovrà cliccare "Ulteriori informazioni → Esegui comunque". Per eliminare
l'avviso: firma Authenticode (signtool) oppure SmartScreen "più info" dopo
qualche installazione.

## 5. Log dell'app sull'altro PC

`%APPDATA%\cfmesh-autogui\logs\app.log` — chiedi all'amico di incollarlo
se qualcosa non va.

## 6. Se in futuro serve il pacchetto CON WSL/OpenFOAM

- `installer/setup_wsl_openfoam.ps1` installa WSL2 + OpenFOAM v2512 +
  cfMesh sull'altro PC (admin, ~1-2 GB di download).
- Dopo il setup, tutti i percorsi (cfMesh, checkMesh) funzionano.
