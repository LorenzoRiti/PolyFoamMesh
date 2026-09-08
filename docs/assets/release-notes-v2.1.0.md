## PolyFoamMesh 2.1.0

Prima release pubblica: generatore di mesh poliedriche per OpenFOAM, con
convertitore tet→poly (dual baricentrico) e motore di boundary layer
scritti da zero, sopra GMSH e cfMesh.

### Installazione

Scarica `PolyFoamMesh-2.1.0-Setup.exe` qui sotto, doppio clic, installa
(nessun Python richiesto). Se Windows SmartScreen avvisa: "Ulteriori
informazioni" → "Esegui comunque" — l'exe non è firmato con un
certificato a pagamento, è un falso positivo comune per software di
sviluppatori indipendenti.

Guida completa: [docs/INSTALL.md](../../blob/main/docs/INSTALL.md)

### Cosa funziona subito (senza WSL)

| Percorso | Risultato |
|---|---|
| CFD Poly (GMSH, no-WSL) | Tet GMSH → dual → mesh 100% poliedrica |
| FEM Tetra (GMSH, no-WSL) | Mesh tetraedrica per solver FEM |
| Visualizzazione | Viewer 3D di geometria e mesh |

### Cosa serve WSL2 + OpenFOAM

Il percorso **Cartesian cfMesh** e la **validazione checkMesh** richiedono
WSL2 + OpenFOAM v2512, non inclusi nell'installer. Script di setup:
`installer/setup_wsl_openfoam.ps1`.

### Requisiti

Windows 10/11 64-bit, ~2 GB RAM libera, ~2.5 GB spazio su disco.

### Limiti noti

STL auto-intersecanti o con fori non tagliati possono far fallire GMSH.
Geometrie concave con difetti pre-esistenti possono impedire la chiusura
watertight del dual poliedrico. Elenco completo e onesto, senza spin:
[docs/residual_risks.md](../../blob/main/docs/residual_risks.md).

### Sviluppo

Progetto sviluppato con un workflow human-in-the-loop: l'architettura e
le decisioni fisiche sono dell'autore, l'IA ha accelerato la stesura del
codice sotto quella direzione. Dettagli:
[AI_COLLABORATION.md](../../blob/main/AI_COLLABORATION.md).
