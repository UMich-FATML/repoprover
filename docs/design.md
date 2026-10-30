# Design

What RepoProver believes, and the decisions it has made. Read this before
changing behavior; log new decisions here with date and status.

## Core beliefs

1. **The repo is the unit of work.** Agents collaborate through git branches
   (PRs), a file-system issue tracker (`issues/*.yaml`) and `CONTENTS.md` —
   not through orchestrator state. State (`.repoprover/state.json`) records
   progress; it does not define it.
2. **`main` always builds.** The merge queue re-verifies with `lake build`
   after every merge. A PR that breaks the build never lands.
3. **Every change is reviewed.** Math review checks faithfulness to the
   source; engineering review checks Lean quality. Both must approve.
4. **Sources are ground truth and read-only.** Agents never edit tex. When
   the source is ambiguous, the reviewer arbitrates — silently "fixing" the
   math is the failure mode.
5. **Statements before proofs.** Sketchers translate statements with
   `sorry` markers; provers fill them. Wrong statements are caught by review
   before proof effort is spent.
6. **Honest incompleteness beats fake completeness.** `sorry -- [cited]`
   (result quoted from the literature, not proved in this source) and
   `sorry -- [exercise]` (source leaves it to the reader) are tracked, legal
   states. Placeholder or tautological "proofs" are critical review issues.

## Books and papers are one pipeline

A project formalizes a **source** — a book or a paper. `SourceKind`
(`src/repoprover/manifest.py`) exists so prompts, CLI output and docs can say
"chapter" or "section"; the run loop, review flow, state format and wire
format are identical. Paper-specific *wording* (main-theorem placement,
standing assumptions, numbered equations, cited results) lives in prompt
guidance blocks (`PAPER_SKETCH_GUIDANCE`), not in new code paths.

## Decision log

| Date | Decision | Why |
|------|----------|-----|
| 2026-10-19 | Internal unit key stays `chapter_id` everywhere (state, recording events, distributed tasks, viewer) | Renaming the wire/state key breaks every resumed run and recording; a display-only rename is not worth that. `project_id` *was* renamed from `book_id` because state migration is a two-line fallback. |
| 2026-10-19 | Manifests keep `chapters` for books and add `sections` for papers; loaders accept both keys for either kind | Papers deserve honest manifests, but one canonical key per kind avoids a forced migration and keeps `git log` on manifests readable. |
| 2026-10-19 | The initial-sketch issue origin marker stays the literal string `"initial chapter sketch"` | Coordinator resume logic matches it; changing the string orphans every in-flight run. |
| 2026-10-19 | `init-paper` uses stdlib `urllib` for the arXiv fetch | One HTTPS GET; a dependency would be all cost, no benefit. |
| 2026-10-19 | PDF-only papers are refused, never OCR'd | Math OCR noise produces *plausible but wrong* statements — reviewers cannot be counted on to catch subtle sign/coercion errors, and a bad statement poisons every downstream proof. A clean refusal with the `--source` remedy fails safely. |
| 2026-10-19 | `init-paper` never runs `lake` or `git` | Ingestion must stay side-effect-free and reviewable: it writes files, prints next steps, and stops. Users keep control of their toolchains. |
| 2026-10-19 | Target ids map LaTeX labels `:` → `.` (`thm:main` → `thm.main`) | Matches the Lean declaration style of the book projects, so target names read as Lean names and grep cleanly. |
| 2026-10-19 | No CI workflows; local hooks only (`pixi run` tasks + `.githooks/pre-commit`) | The repo's gate is `pixi run check`; a remote CI would need secrets and mirrors this repo does not have. |
| 2026-10-19 | `E501` ignored; ruff line-length 120 | Long prompt strings and table rows; format owns the line length, review owns readability. |
| 2026-10-19 | Coordinator state saves both `project_id` and `source_kind`; load falls back to legacy `book_id`, defaulting `source_kind` to `book` | Old runs resume unchanged; new runs record what they are. |
| 2026-10-19 | Default LLM is `kimi-k3` on the comet gateway, called directly through the OpenAI SDK; no external agent harness (e.g. dsh) | RepoProver already owns its tool loop and gives tools to the model itself, so a harness would only duplicate it. The gateway URL comes from its state file because the gateway moves between nodes. |
| 2026-10-19 | Import-header merge conflicts where one side only adds imports are resolved mechanically (all imports from both sides, then the other side's remaining lines), in both the pre-review merge and the merge to `main`; any other conflict still goes back to the agent | In 11 book runs, ~half of all sketch rejections were conflicts, mostly on the root module's import list, which has one correct resolution. The build check after the merge still gates the result. |
| 2026-10-19 | A unit's `lean_path` file is owned by its sketcher; scan/triage/maintain/progress prompts forbid scaffolding it or filing issues about it | Support agents read missing `lean_path` files as drift and committed skeletons, so every in-flight sketch then conflicted on add/add. |
| 2026-10-19 | `manifest.json` and source `.tex`/`.bib` are read-only for agents: a PR touching them is bounced before review, with undo instructions | In 11 book runs agents renamed unit ids and tex files and edited labels mid-run; the coordinator then tracked duplicate units and reviewers compared against altered sources. File tools already refused `.tex` writes, but `bash`/`git mv` bypassed them, so the gate checks the PR diff. |
| 2026-10-21 | `state.json` is written write-then-rename, and a failed save is logged and retried instead of crashing the coordinator | A briefly full shared filesystem ended two book runs (save_state raised, the coordinator drained) and truncated a third run's state.json to 0 bytes, making it unresumable. |
| 2026-10-24 | A failing main-loop iteration is logged and retried with backoff (10 s doubling to 300 s); only 20 consecutive failures crash the coordinator. After a failure the base checkout is returned to a clean `main`. | In the 11-book runs every coordinator crashed within about a day of each restart, mostly on transient ENOSPC from the shared filesystem, once on a state dict mutated by a review thread mid-save. Each crash then drained for hours and exited, idling the run. |
| 2026-10-30 | Contributor prompts carry a "Verification discipline" section: `lean_check` per declaration, `lake build <Module>` at most twice per task, no bare `lake build`/`lake env lean` on own files, no re-reading files or guessing toolchain paths | In 11 h the book runs issued 12,633 whole-project builds (4.1% of turns, vs 0.6% under the previous model); they cost minutes of shared CPU each and starve every other agent's Lean checks, which showed up as ~1.7k REPL timeouts. |
