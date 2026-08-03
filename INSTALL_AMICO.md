# CFMesh-AutoGUI 2.1.0 — guida per l'amico

Pacchetto **standalone** (niente Python da installare): un solo installer
per PC **Windows 10/11 a 64 bit**.

## 1. Requisiti del PC
- Windows 10 o 11, 64 bit (x64).
- ~2 GB di RAM libera (consigliato 4+).
- ~2.5 GB di spazio su disco.
- **Non serve Python**, non serve nient'altro.

## 2. Installazione
1. Doppio clic su `CFMesh-AutoGUI-2.1.0-Setup.exe`.
2. Se Windows SmartScreen avvisa ("Windows ha protetto il PC"):
   - **"Ulteriori informazioni" → "Esegui comunque"**. L'exe non è firmato
     con un certificato a pagamento (costa ~100 €/anno): è un falso
     positivo; il pacchetto è generato da questa macchina di sviluppo.
3. Completa la procedura (lingua italiana disponibile). Finisce con
   l'avvio dell'app.
4. Da quel momento: **Start → CFMesh-AutoGUI** (o l'icona sul desktop).

## 3. Cosa puoi provare (funziona senza WSL)
| Percorso | Pulsante | Risultato |
|---|---|---|
| **CFD Poly (GMSH, no-WSL)** | Mesh tab → 2° pulsante | Tet GMSH → **dual → 100% poliedrica, stile STAR-CCM+** (la topologia che interessa) |
| **FEM Tetra (GMSH, no-WSL)** | Mesh tab → 3° pulsante | Mesh tetraedrica per solver FEM |
| Visualizzazione | — | Viewer 3D della geometria e del mesh |

Prova veloce consigliata:
1. **File → Load Geometry...** → apri un STL (es. `venturi.stl`, chiuso/watertight).
2. Mesh tab → **"CFD Poly (GMSH, no-WSL)"**.
3. Case directory: un percorso **senza spazi** (es. `C:\prova`).
4. **Run** → nel log: GMSH tet → dual → mesh 100% poliedrica.

## 4. Cosa NON è incluso (percorsi WSL)
- **Cartesian cfMesh** e la **validazione checkMesh** richiedono
  **WSL2 + OpenFOAM**, che l'installer **non** installa (scelta demo).
  Se li selezioni, l'app mostra un avviso chiaro e non procede.

## 5. Disinstallazione
**Start → CFMesh-AutoGUI (cartella) → Disinstalla** oppure
Impostazioni → App → CFMesh-AutoGUI → Disinstalla.
Rimuove tutto (app + associazioni file + scorciatoie); eventuali log
restano in `%APPDATA%\cfmesh-autogui\logs` (puoi cancellare la cartella).

## 6. Limiti noti (onesti)
- **STL auto-intersecanti** (triangoli sovrapposti, fori non tagliati nel
  CAD) possono far fallire GMSH con "PLC Error: two segments intersect" —
  usare STL esportati puliti.
- Formati consigliati: **STL chiuso** (watertight) o **STEP**.
- I moduli opzionali (snappyHexMesh, MMG, cfMesh) possono non essere
  presenti nel pacchetto demo.

## 7. File del pacchetto (per chi sviluppa)
```
installer/output/CFMesh-AutoGUI-2.1.0-Setup.exe   <- DA DARE ALL'AMICO
dist/CFMesh-AutoGUI/                              <- bundle one-dir (1.3 GB)
```
Ricompilare da sorgente: `pyinstaller --noconfirm --clean CFMesh-AutoGUI.spec`
poi `installer\ISCC.exe installer\inno_setup.iss`.
