# Contributing to PolyFoamMesh

Grazie per l'interesse. Il progetto è giovane come open source: issue e PR
sono benvenute, ma tieni presente che è mantenuto da una persona sola.

## Setup ambiente di sviluppo

```bash
pip install -e ".[test]"
pip install ruff
```

Requisiti runtime: Python 3.11+, GMSH (`pip install gmsh`), opzionalmente
OpenFOAM v2512 in WSL2 per i percorsi cfMesh/checkMesh — vedi [README](README.md).

## Prima di aprire una PR

```bash
ruff check src/ tests/
pytest tests/ -m "not slow and not wsl" -q
```

Questi due comandi sono lo stesso gate che gira in CI
([.github/workflows/ci.yml](.github/workflows/ci.yml)). I test marcati `slow`
o `wsl` richiedono un ambiente Windows+WSL2+OpenFOAM reale e non girano su
CI: eseguili in locale se la tua modifica tocca quei percorsi.

## Segnalare un bug

Apri una issue con: geometria di input (o una minimale che riproduce il
problema), comando/percorso usato in GUI, log da `%APPDATA%\cfmesh-autogui\logs\app.log`.

## Limiti noti

Prima di segnalare un comportamento come bug, controlla
[docs/residual_risks.md](docs/residual_risks.md) — elenca i limiti già
noti e documentati (timeout su mesh molto grandi, casi di geometria concava,
ecc.).

## Convenzione sui commit

Per rendere tracciabile chi ha scritto cosa, i commit da qui in avanti
usano un prefisso:

- `[AI-gen]` — codice generato con assistenza IA, poi revisionato da un
  ingegnere umano prima del commit
- `[Human-fix]` — correzioni di logica, algoritmi o decisioni di design
  scritte direttamente da una persona

## Uso dell'IA nei contributi

Il progetto stesso usa l'IA come strumento di pair-programming (vedi
[AI_COLLABORATION.md](AI_COLLABORATION.md)) — non c'è alcun problema a
farlo anche nella tua PR. Se usi un assistente IA per scrivere parte del
codice, dichiaralo nella descrizione della PR: aiuta chi fa la review a
sapere dove concentrare l'attenzione, in particolare su tolleranze
numeriche, criteri di qualità mesh e gestione errori, dove serve verifica
umana indipendentemente da chi ha scritto le righe.

## Licenza dei contributi

Il progetto è GPLv3 ([LICENSE](LICENSE)). Contribuendo accetti che il tuo
codice venga distribuito sotto la stessa licenza.
