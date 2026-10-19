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
