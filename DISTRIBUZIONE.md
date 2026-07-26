# Come distribuire CFMesh-AutoGUI a un amico

L'app è divisa in due pezzi che vanno distribuiti separatamente:

1. **L'eseguibile Windows** (`CFMesh-AutoGUI.exe`) — l'interfaccia grafica, il meshing GMSH,
   tutto quello che gira su Windows. Questo si può impacchettare e mandare così com'è.
2. **OpenFOAM v2512 + cfMesh dentro WSL2** — il motore di meshing vero e proprio
   (`cartesianMesh`). Questo **non può essere incluso nell'exe**: è un pacchetto Linux di
   diversi GB che deve essere installato nella distro WSL del tuo amico. Non c'è modo di
   evitarlo, ma il setup è automatizzabile con lo script qui sotto.

## 1. Costruire l'exe (lato tuo, una volta sola)

```bash
python -m PyInstaller CFMesh-AutoGUI.spec --noconfirm
```

Il risultato è `dist/CFMesh-AutoGUI.exe` — un eseguibile standalone che include già Python,
PySide6, cadquery, trimesh, pyvista, gmsh, pymeshfix: il tuo amico non deve installare
Python né pip install di niente per la parte GUI.

> **Nota per il futuro**: la prima build fatta oggi (715 MB) crashava all'avvio con
> `DLL load failed while importing _casadi` — PyInstaller aveva impacchettato `casadi`,
> `torch`, `cv2`, `pyarrow` e altra roba completamente estranea all'app, presa dall'ambiente
> Python globale di questo PC (usato anche per altri progetti). Confermato via grep che
> nessuno di questi pacchetti è mai importato dal codice di cfmesh-autogui. Aggiunti alla
> sezione `excludes` di `CFMesh-AutoGUI.spec` — se in futuro l'exe torna a gonfiarsi o
> ricompare un crash "DLL load failed" simile, controlla lì prima di tutto.

Zippalo e mandaglielo (email/drive/chiavetta — è troppo grande per la maggior parte delle
chat).

## 2. Cosa deve fare il tuo amico

### 2a. Scompattare e lanciare l'exe

Basta estrarre lo zip e fare doppio click su `CFMesh-AutoGUI.exe`. Non serve installazione.
Al primo avvio, se WSL2/OpenFOAM non sono ancora pronti, l'app lo segnala chiaramente
invece di bloccarsi (fix di oggi) — ma senza OpenFOAM il meshing vero e proprio non parte.

### 2b. Installare WSL2 + OpenFOAM v2512 (una tantum, ~10-15 minuti + download)

Da PowerShell **come amministratore**:

```powershell
wsl --install -d Ubuntu
```

Riavvia se richiesto, poi apri Ubuntu dal menu Start e crea l'utente Linux che ti chiede
al primo avvio (username/password a piacere, sono solo per la VM Linux).

Poi, **dentro Ubuntu** (il terminale che si apre), installa OpenFOAM v2512:

```bash
curl -s https://dl.openfoam.com/add-debian-repo.sh | sudo bash
sudo apt-get update
sudo apt-get install -y openfoam2512-default
```

Questo installa OpenFOAM **e cfMesh insieme** (`cartesianMesh` è incluso nel pacchetto
`openfoam2512-default`, non serve installarlo a parte — verificato: `cartesianMesh` si trova
in `/usr/lib/openfoam/openfoam2512/platforms/.../bin/` dopo questo comando).

Verifica che sia andato a buon fine:

```bash
source /usr/lib/openfoam/openfoam2512/etc/bashrc
cartesianMesh -help
```

Se stampa le opzioni del comando, è tutto pronto. A questo punto CFMesh-AutoGUI trova
OpenFOAM automaticamente (usa la distro WSL di default, chiamata "Ubuntu").

### 2c. (Opzionale) Script automatico

Se preferisci automatizzare il passo 2b, nella cartella `installer/` c'è
`setup_wsl_openfoam.ps1` — un unico script PowerShell che fa `wsl --install`, aspetta il
riavvio se serve, e poi lancia l'installazione di OpenFOAM dentro Ubuntu. Vedi sotto per
come generarlo/usarlo.

## 3. Requisiti minimi sul PC dell'amico

- Windows 10 (build 19041+) o Windows 11, 64 bit
- Virtualizzazione abilitata nel BIOS (necessaria per WSL2 — di solito è già attiva)
- ~10 GB liberi (OpenFOAM dentro WSL2 pesa parecchio)
- Consigliati 8+ GB di RAM: ogni core usato nel meshing parallelo carica una copia della
  geometria in memoria, quindi macchine con poca RAM dovrebbero usare il meshing seriale
  (l'app ora clampa automaticamente i core se rileva poca memoria disponibile in WSL2)

## 4. Alternative all'exe standalone

- **Installer .exe con wizard** (Inno Setup): c'è `installer/installer.iss`, ma **non è
  pronto per essere compilato così com'è** — referenzia `LICENSE`, `build/icon.ico` e
  `installer/vc_redist.x64.exe` che al momento non esistono nel repo. Per un singolo amico
  che deve solo provarla, non ne vale la pena: usa lo zip dell'exe (punto 1). Se in futuro
  vuoi un vero installer con icona e disinstallazione da Pannello di Controllo, vanno creati
  quei tre file prima di lanciare `iscc installer/installer.iss`.
- **pip install** (se il tuo amico ha già Python 3.11+): `pip install -e .` dal sorgente,
  poi `cfmesh-autogui` da terminale. Più macchinoso ma niente file da 330 MB da mandare.
