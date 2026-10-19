# Tech debt

Known, accepted debts — each with why it is safe to keep and what paying it
off would look like. Files over 1500 lines must be listed here (enforced by
`scripts/lint.py`).

## Oversized files (grandfathered)

| File | Lines | Why kept | Payoff |
|------|-------|----------|--------|
| `src/repoprover/coordinator.py` | ~3.5k | The coordinator is one state machine; splitting it mid-flight risks subtle resume bugs, and it has no test coverage to lean on | Extract PR review/merge queue into a module, with characterization tests first |
| `src/repoprover/viewer.html` | ~4.2k | Single-file trajectory viewer; self-contained by design | Split into modules only if it needs features |

## Testing gaps

- **coordinator.py has no direct tests** — behavior is covered end-to-end by
  `examples/toy_project` smoke runs only. Any refactor should start with
  characterization tests of `RunState` save/load and the PR lifecycle.
- **`examples/toy_paper` has no end-to-end run test** — it needs Mathlib and
  an LLM key; exercised manually via `setup.sh`.

## Ingestion limitations (accepted, documented in `docs/sources.md`)

- **No `\input` inside verbatim/`\begin{comment}`** — flattening is textual;
  an `\input` inside a verbatim block would be inlined. Rare in practice.
- **No `\includeonly` support** — papers using `\includeonly` still get all
  `\include`d files inlined.
- **Section split is line-based** — a `\section` command split across lines
  is missed (rare); `\section*`/optional-arg forms are handled.
- **No `[assumption]` marker** — standing assumptions are covered by prompt
  guidance (`PAPER_SKETCH_GUIDANCE`) rather than a tracked marker like
  `[cited]`/`[exercise]`. Add one only if review data shows agents hiding
  them.

## Tooling

- **pyright is in basic mode** — strict mode would need annotations across
  the mixins and `Any`-typed LLM client plumbing. Ratchet up per-module.
- **No CI** — local hooks only (`pixi run check`); remote CI would need
  secrets/mirrors this repo does not have (decision in `docs/design.md`).
- **`CONTRIBUTING.md` style section** is a pointer to `AGENTS.md`; if real
  contributor docs diverge, split them properly.
