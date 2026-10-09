---
name: Pull Request
about: Proposed change
title: ""
labels: []
assignees: []
---

## What it does

<!-- One or two sentences: what changes, why. -->

## Checks performed

Before opening the PR, make sure the CI gate passes locally:

```bash
ruff check src/ tests/
pytest tests/ -m "not slow and not wsl" -q
```

- [ ] `ruff check` green
- [ ] fast suite green (or `slow`/`wsl` run locally if the change touches those paths)
- [ ] no new dependency without a reason (and no GPL-incompatible dependencies)

## Notes for the reviewer

<!-- What to look at carefully, any trade-offs, what you left out. -->

## Touched files

<!-- Short list: the most relevant files, not the output of git status. -->
