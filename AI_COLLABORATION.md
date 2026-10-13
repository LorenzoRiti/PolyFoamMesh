# Collaborazione con l'IA

Questo progetto usa l'intelligenza artificiale come strumento di
pair-programming, non come sostituto dell'autore. Lo dichiaro qui in
modo esplicito perché credo sia più utile essere trasparenti sul workflow
reale che lasciarlo intuire.

## Come funziona in pratica

Il workflow è a due ruoli netti:

- **L'umano progetta la fisica e l'algoritmo**: la scelta del dual
  baricentrico per la conversione tet→poly, il motore dei boundary layer,
  le tolleranze numeriche, le strategie di fallback quando un mesh non
  chiude, l'interpretazione dei risultati di `checkMesh`. Sono decisioni
  che richiedono capire cosa succede fisicamente a una mesh, non solo far
  compilare il codice.
- **L'IA scrive il boilerplate sotto la mia direzione**: interfaccia
  grafica (PySide6), test unitari, template di configurazione, parser di
  log, script di packaging/installer. Codice ripetitivo o meccanico dove
  la scelta di design è già stata fatta da me, e serve "solo" scriverlo
  bene.

Una stima onesta: circa il **60% delle righe di codice** è stato scritto
con l'assistenza di un'IA generale; il **100% è stato revisionato** da me
prima di essere accettato — letto, testato, e quando necessario corretto o
riscritto.

## Chi ha fatto cosa

| Componente | Autore principale |
|---|---|
| GUI (PySide6), pannelli, wizard | IA, sotto direzione umana |
| Suite di test (~1.100 test) | IA, sotto direzione umana |
| Template di mesh/case, parsing log | IA, sotto direzione umana |
| Script di installer/packaging | IA, sotto direzione umana |
| Dual baricentrico (conversione tet→poly) | Umano |
| Motore boundary layer | Umano |
| Tolleranze numeriche e criteri di qualità mesh | Umano |
| Gestione errori, fallback e rollback | Umano |

## Responsabilità

L'autore umano è pienamente responsabile del funzionamento e della
validità fisica del codice. L'IA è stata uno strumento di supporto, non
un autore autonomo: ogni riga che genera codice è stata rivista, testata
o corretta da una persona prima di entrare nel progetto. I numeri di test
e qualità mesh citati nel [README](README.md) sono misurati, non
inventati.
