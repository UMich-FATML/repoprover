# AGENTS.md

RepoProver: multi-agent scaffold that formalizes math **books and papers** into
Lean 4. Sketcher agents translate LaTeX, prover agents fill proofs, reviewer
agents gate every merge; a coordinator runs the loop (git branches as PRs,
merge queue keeps `main` building).

## Map

- `src/repoprover/coordinator.py` — the run loop, PRs, merges, state (largest
  file; see `docs/tech-debt.md` before splitting it)
- `src/repoprover/manifest.py` — manifest schema (`SourceKind`, `Unit`,
  `Manifest`, `load_manifest`); **the** definition of book/paper projects
- `src/repoprover/paper_ingest.py` — `init-paper`: arXiv/tex → project
  (fetch, flatten, split, target extraction)
- `src/repoprover/cli.py` — subcommands (`run`, `status`, `export`,
  `init-paper`)
- `src/repoprover/agents/` — agent runtimes: `contributor.py` (sketch/prove/
  maintain/... modes + prompts), `reviewers.py` (math/engineering reviews),
  `base.py`, mixins `*_tools.py`
- `src/repoprover/distributed.py` — ZMQ task queue for multi-node runs;
  `stool.py` — SLURM launcher
- `src/repoprover/types.py`, `recording.py`, `lean_checker.py`,
  `git_worktree.py` — shared types, run recording, Lean REPL, worktrees
- `docs/` — system of record (see below); `examples/` — toy book + toy paper
- `tests/` — pytest; `scripts/` — analysis tools + `lint.py`

## Commands (pixi)

```bash
pixi run lint           # ruff + custom repo lints (scripts/lint.py)
pixi run format         # ruff format (in place)
pixi run format-check   # ruff format (check)
pixi run typecheck      # pyright (basic)
pixi run test           # pytest
pixi run check          # all of the above, read-only
```

Pre-commit runs `lint` + `format-check` (see `.githooks/`; enabled by
`bash scripts/install.sh`). Use `pixi run` for everything; do not pip install.

## Docs are the system of record

- `docs/design.md` — core beliefs + decision log (read before design changes)
- `docs/sources.md` — manifest schema, tex layout, `init-paper`, label→target
  rules, `[cited]`/`[exercise]` markers, PDF-only policy
- `docs/project-setup.md` — what a target Lean project needs before a run
- `docs/plans/` — one file per effort, `Status:` line at top
- `docs/tech-debt.md` — known debts; a file >1500 lines must be listed there

If a rule is not written down here or in `docs/`, it does not exist; if code
and docs disagree, fix whichever is wrong in the same change.

## Rules that keep agents effective

1. Books and papers are the same pipeline: `SourceKind` changes **wording**
   (prompts, CLI output, docs) — never control flow. The internal unit key is
   `chapter_id` everywhere (state, events, wire format, viewer) for both kinds.
2. Manifests: books use the `chapters` key, papers use `sections`; loaders
   accept both for either kind. Never break old `state.json` files.
3. `init-paper` never runs `lake` or `git`, and never OCRs a PDF (see
   `docs/design.md` for why). Failures must name the fix in the message.
4. Source tex is read-only; the run loop, review flow and state format are
   identical for books and papers.
5. Commit messages follow the trailer convention in the repo (`DSH-session:`).
