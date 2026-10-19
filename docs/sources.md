# Source projects (books and papers)

How RepoProver consumes a mathematical source. A **source project** is a git
repo containing a Lean project, the tex sources, a `manifest.json` and an
`issues/` directory. See [project-setup.md](project-setup.md) for the Lean/git
side and [design.md](design.md) for the reasoning.

## Quick start: papers

```bash
# From an arXiv id (downloads and unpacks the e-print):
repoprover init-paper /path/to/project --arxiv 2506.00738

# From local tex (main file, directory, or .tar.gz e-print):
repoprover init-paper /path/to/project --source paper.tex

# Then (init-paper does not run these):
cd /path/to/project
lake update && lake build && lake build REPL
git init -b main && git add -A && git commit -m "Import paper"
repoprover run .
```

`init-paper` writes `manifest.json`, `CONTENTS.md`, `tex/` (preamble, one
file per top-level `\section`, `all.tex`, bibliography files) and `issues/`,
then prints the remaining setup steps. Options: `--project-name` (lake
project name), `--tex-dir` (default `tex`), `--split none` (one unit for the
whole paper), `--force` (overwrite an existing manifest/tex).

Books are prepared the same way as before: commit tex split by chapter, write
`manifest.json` by hand (or adapt `configs/example_manifest.json`).

### PDF-only papers {#pdf-only-papers}

Some arXiv papers have no LaTeX source (scanned or PDF-only submissions).
`init-paper` detects this from the e-print's magic bytes and **refuses**
rather than OCR. OCR'd math looks plausible while being wrong, and wrong
statements poison every proof downstream. The remedy is to get the authors'
tex (project page, personal site, or email) and rerun with `--source`.

## Manifest format

`manifest.json` at the project root. Books use the `chapters` key, papers use
`sections`; loaders accept either key for either kind, but a manifest with
both (or neither) is rejected.

```json
{
  "kind": "paper",
  "title": "Doubling Properties of the Natural Numbers",
  "arxiv_id": "2506.00738",
  "source_url": "https://arxiv.org/abs/2506.00738",
  "sections": [
    {
      "id": "results",
      "title": "Results",
      "source_path": "tex/02-results.tex",
      "target_theorems": ["thm.double-add", "lem.double-even"]
    }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `kind` | Yes | `"book"` or `"paper"` (wording only — see [design.md](design.md)) |
| `title` | Yes | Human-readable source title |
| `chapters` / `sections` | Yes (one) | List of units (below) |
| `arxiv_id` | No | arXiv id for papers |
| `source_url` | No | Where the source came from |

Unit fields: `id` (unique, slug-style), `title`, `source_path` (tex file,
relative to project root, committed to git), optional `lean_path`, optional
`target_theorems` (list of result names; issues are auto-created for each).

The schema is defined in `src/repoprover/manifest.py` (`load_manifest`),
which every consumer uses — do not parse manifests by hand elsewhere.

## Tex layout

```
tex/
├── preamble.tex   # everything before \begin{document} (papers)
├── all.tex        # the whole flattened source, for reference
├── 00-front-matter.tex   # abstract etc. (papers, split mode)
├── 01-introduction.tex   # one file per top-level \section
└── refs.bib       # bibliography (readable, read-only)
```

Tex files (and `.bib`/`.bbl`) are **read-only** for agents: they are the
ground truth reviewers check Lean against.

## Label → target convention

Papers: a **result** is an occurrence of a theorem-like environment
(`theorem`, `proposition`, `lemma`, `corollary`, `definition`, `conjecture`,
plus anything the preamble declares with `\newtheorem`). A result's target id
is its first `\label{...}` with `:` mapped to `.`:

| LaTeX | Target id |
|-------|-----------|
| `\begin{theorem}\label{thm:main}` | `thm.main` |
| `\begin{lemma}\label{lem:aux}` | `lem.aux` |

Results without a label are counted and reported by `init-paper` (add a
`\label` to the tex to make them targets). This matches the Lean declaration
naming style of the book projects.

## `sorry` markers

Agents mark honest incompleteness on the `sorry` line:

- `sorry -- [cited]` — the result is quoted from the literature (`\cite`)
  and no tex file in the project proves it. Valid **only** if the source
  really does not prove it; results the paper proves itself must not be
  `[cited]`.
- `sorry -- [exercise]` — the source leaves the proof to the reader.

Reviewers reject misuse of either marker as a critical issue.
