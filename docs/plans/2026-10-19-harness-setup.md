# Plan: Harness setup (pixi toolchain, lints, docs)

Status: Complete (2026-10-19)

## Goal

Make the repo legible and safe for coding agents: one command per check,
custom lints for repo-specific invariants, docs as the system of record.

## Changes

1. **`pixi.toml` (new)** — tasks `lint`, `format`, `format-check`,
   `typecheck`, `test`, `check`, `install`, `toy-book`, `toy-paper`; deps:
   pytest, ruff, pyright (needs nodejs), editable `repoprover` install;
   `analysis` feature (matplotlib/numpy) for the scripts.
2. **`pyproject.toml`** — pytest `pythonpath=["src"]` (tests run from a clean
   checkout without installing), ruff config (line-length 120, E501 ignored,
   E/F/I/W/B/UP selected), pyright basic mode, `pyzmq` as a real dependency
   (it was imported but undeclared).
3. **Lint cleanup** — 50 ruff findings and 29 pyright errors fixed
   (mixin annotations, `Optional` handling, narrow zmq JSON union, dict
   annotations, `raise ... from e`).
4. **`.githooks/pre-commit` + `scripts/install.sh`** — local hooks run
   `pixi run lint` + `pixi run format-check`; no CI by design
   (see `docs/design.md`).
5. **`scripts/lint.py` (new) + `tests/test_lint.py`** — repo invariants:
   AGENTS.md size, relative links resolve, plans have `Status:`, example
   manifests load, prompt placeholders are the documented set, files >1500
   lines must be listed in `docs/tech-debt.md`.
6. **Docs** — `AGENTS.md` (map/commands/rules) + `CLAUDE.md` pointer;
   `docs/design.md`, `docs/sources.md`, `docs/project-setup.md` (renamed
   from `docs/README.md`, stale bits fixed), `docs/tech-debt.md`, this plan.
