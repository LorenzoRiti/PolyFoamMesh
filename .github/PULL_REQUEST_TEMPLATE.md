---
name: Pull Request
about: Proposta di modifica
title: ""
labels: []
assignees: []
---

## Cosa fa

<!-- Una o due frasi: cosa cambia, perché. -->

## Verifiche fatte

Prima di aprire la PR assicurati che il gate di CI passi in locale:

```bash
ruff check src/ tests/
pytest tests/ -m "not slow and not wsl" -q
```

- [ ] `ruff check` verde
- [ ] suite fast verde (o `slow`/`wsl` eseguita in locale se la modifica tocca quei percorsi)
- [ ] nessuna dipendenza nuova senza motivo (e niente dipendenze GPL-incompatibili)

## Note per il reviewer

<!-- Cosa guardare con attenzione, eventuali trade-off, ciò che hai lasciato fuori. -->

## File toccati

<!-- Elenco breve: file più rilevanti, non l'output di git status. -->
