# Plan: Paper formalization support

Status: Complete (2026-10-19)

## Goal

RepoProver formalizes research papers with the same pipeline as books,
onboarded by one command.

## Changes

1. **`src/repoprover/manifest.py` (new)** — `SourceKind` (BOOK/PAPER with
   project/unit nouns), `Unit`, `Manifest`, `load_manifest` (accepts
   `chapters` or `sections` for either kind; legacy `source` alias), 
   `render_contents_md`, `initial_sketch_issues`. Single definition of the
   manifest schema; error messages point at `docs/sources.md`.
2. **Renames** — `BookCoordinator` → `Coordinator`, `BookCoordinatorConfig`
   → `CoordinatorConfig`, `book_id` → `project_id` (state load falls back to
   the old key; `source_kind` recorded, default `book`). Internal unit key
   stays `chapter_id` (see `docs/design.md`).
3. **Kind-aware prompts** — `ContributorTask.source_kind` threaded from the
   coordinator through every construction site and the ZMQ wire format;
   sketch/prove/progress prompts pick nouns from the kind; papers get
   `PAPER_SKETCH_GUIDANCE` (main-theorem placement, standing assumptions,
   numbered equations, cited results, appendices) and a matching reviewer
   checklist block.
4. **`src/repoprover/paper_ingest.py` (new) + `repoprover init-paper`** —
   arXiv fetch (stdlib urllib, magic-byte sniffing; PDF-only and non-tex
   e-prints refused cleanly, never OCR'd), `\input`/`\include` flattening,
   split by top-level `\section` (front matter + appendix handling), labeled
   result extraction (`thm:x` → `thm.x`), writes
   manifest/CONTENTS/tex/issues; never runs lake or git.
5. **`examples/toy_paper/`** — two-section paper with labeled
   theorem/lemma/corollary, an unlabeled corollary, an appendix and a
   bibliography; `setup.sh` ingests it via `init-paper` and scaffolds the
   lake project.
6. **`.bib`/`.bbl` read-only** — agents can consult the bibliography for
   `[cited]` decisions without editing it.

## Tests

`tests/test_manifest.py` (schema, both keys, errors), 
`tests/test_contributor_prompts.py` (every prompt template formats for both
kinds; paper guidance only for papers),
`tests/test_paper_ingest.py` (flatten, split, targets, PDF refusal writes
nothing, arXiv path with injected fetch),
`tests/test_state_compat.py` (legacy state files load).
