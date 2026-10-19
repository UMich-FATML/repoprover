# Project setup

Multi-file autoformalization system for Lean 4. Takes mathematical sources —
textbooks or research papers (LaTeX) — and produces verified Lean 4
formalizations. Papers can be onboarded automatically with
`repoprover init-paper` (see [sources.md](sources.md)); this page describes
what the resulting project needs before a run.

## Base Project Setup (before first run)

Before launching a run, the base Lean project must be properly set up. The agents work in git worktrees that inherit committed files and symlinked Lake packages from the base project, so all of the following must be in place.

### 1. Git repo with `main` branch

The project must be a git repository with at least one commit **on the `main` branch**. RepoProver requires the default branch to be named `main` (not `master`).

```bash
# If your repo uses 'master', rename it to 'main':
git branch -m master main
```

Git worktrees cannot be created from a repo with no commits (the worktrees will be empty and agents won't see any files).

### 2. Source `.tex` files committed

The LaTeX source files referenced in `manifest.json` must be **committed** to the repo. Untracked or staged-only files are not visible in worktrees.

### 3. Lake target for the output directory

The `lakefile.lean` must have a `lean_lib` target that covers the directory where agents write `.lean` files. For example, if `lean_path` entries are under `AlgComb/`, the lakefile needs:

```lean
@[default_target]
lean_lib «AlgComb» where
  globs := #[.submodules `AlgComb]
```

Without this, `lake build AlgComb.MyChapter` will fail with "unknown target".

### 4. Lake dependencies and REPL

Run the following commands in the base project to fetch dependencies and build everything:

```bash
lake update          # Fetch/update Mathlib and other dependencies
lake build           # Build the project (populates .lake/build/)
lake build REPL      # Build the Lean REPL used by agents for interactive proof checking
```

This ensures `.lake/packages/` and `.lake/build/` are fully populated. The worktree manager symlinks `.lake/packages` and `.lake/config` into each agent worktree, so agents reuse the shared Mathlib without re-downloading. See [shared-mathlib-worktrees.md](shared-mathlib-worktrees.md) for details.

**Important:** If your environment rewrites git URLs (e.g., a global `insteadOf` rule mapping `https://github.com/` to `ssh://git@github.com/`), Lake may detect a URL mismatch and try to re-clone packages. Make sure the URLs in `.lake/packages/*/` match what `lake-manifest.json` expects.

### 5. `manifest.json` with unit definitions

The manifest maps unit ids to source and target paths. Books use the
`chapters` key, papers use `sections`; either key is accepted, and `kind`
records which (`"book"` / `"paper"`). The full schema, including the
`target_theorems` label convention, is in [sources.md](sources.md).

```json
{
  "kind": "book",
  "title": "My Book",
  "chapters": [
    {
      "id": "ch1",
      "title": "My Chapter",
      "source_path": "AlgComb/Chapter1.tex",
      "lean_path": "AlgComb/Chapter1.lean"
    }
  ]
}
```

### Checklist

- [ ] Git repo with at least one commit
- [ ] `.tex` source files committed
- [ ] `lakefile.lean` has a `lean_lib` target covering the output directory
- [ ] `lake update && lake build && lake build REPL` succeeded
- [ ] `manifest.json` present with correct paths

## Quick Start

```bash
# Run from your Lean project directory (manifest.json must exist)
cd my-lean-project
repoprover run

# Or specify path
repoprover run ./my-lean-project

# Start fresh (wipe previous run data)
repoprover run --clean
```

The `run` command automatically:
- Finds the manifest file (`manifest.json`, `repoprover.json`, or `.repoprover/manifest.json`)
- Initializes `.repoprover/` directory if needed
- Loads chapters from the manifest
- Resumes from previous state if interrupted

## How It Works

RepoProver uses a simple loop with these agents:

1. **Sketchers** - Translate LaTeX to Lean with `sorry` placeholders
2. **Provers** - Fill in the `sorry` proofs
3. **Maintainers** - Work through open issues in `issues/`

Every change goes through automated review before merging to main.

### Issues Tracking

RepoProver uses a lightweight file-system-based issue tracker:

- Issues are YAML files in the `issues/` directory at the repo root
- Initial issues are auto-generated from `target_theorems` in the manifest
- Provers create issues when they encounter problems they can't solve
- Maintainer agents pick open issues and try to resolve them
- Agents can mark issues as resolved in their pull requests

### The Main Loop

The coordinator runs a simple loop until all chapters are complete:

**Step 1: Launch Sketchers**
- For each chapter without a sketch, create a sketcher agent
- The agent works in an isolated git worktree (branch)
- When done, submit the branch for review

**Step 2: Process Revisions**
- If a PR was rejected, re-run the agent with feedback
- Give up after 5 attempts

**Step 3: Process Reviews**
- For each pending PR:
  - Run `lake build` via centralized build module (auto-reject if it fails)
  - Run math review (mathematical correctness)
  - Run engineering review (code quality)
  - Both must approve (AND-success)

**Step 4: Process Merges**
- For each approved PR:
  - Merge to main with `--no-ff`
  - Verify with `lake build`

**Step 5: Launch Provers**
- After a sketch merges, scan for theorems with `sorry`
- Launch a prover for each one
- Each prover works in its own branch, goes through the same review cycle

Repeat until no `sorry` statements remain.

### Resumability

State is saved to `.repoprover/state.json` after each iteration. If you Ctrl+C and run again, it picks up where it left off.

## Project Structure

```
my-lean-project/
├── lakefile.toml           # Your Lean project
├── MyBook/
│   ├── Chapter1.lean       # Generated Lean files
│   └── Chapter2.lean
├── tex/
│   ├── ch1.tex             # Source LaTeX
│   └── ch2.tex
├── manifest.json           # Chapter definitions
└── .repoprover/
    ├── state.json          # Run state (for resumability)
    ├── learnings.json      # Agent learnings
    └── worktrees/          # Isolated agent workspaces
```

## Manifest Format

The manifest schema (including the paper `sections` key, `kind`, and the
label → target convention) is defined once in [sources.md](sources.md);
`load_manifest` in `src/repoprover/manifest.py` is its implementation.

```json
{
  "kind": "book",
  "title": "My Book",
  "chapters": [
    {
      "id": "ch1",
      "title": "Introduction",
      "source_path": "tex/ch1.tex",
      "target_theorems": ["main_theorem", "key_lemma"]
    },
    {
      "id": "ch2",
      "title": "Basic Results",
      "source_path": "tex/ch2.tex"
    }
  ]
}
```

## CONTENTS.md

When the project is initialized, a `CONTENTS.md` file is generated:

```markdown
# Contents

## TeX Sources

| Chapter | Source | Lean Entry Point | Comments |
|---------|--------|------------------|----------|
| Introduction | `tex/ch1.tex` | | |
| Basic Results | `tex/ch2.tex` | | |

---

## Lean Codebase Overview

*(Document the structure of the Lean codebase here as it evolves:
module hierarchy, shared utilities, naming conventions, etc.)*
```

**Agents are instructed to keep this file updated** when they create, move, rename, or split Lean files. The "Lean Entry Point" can be a file, directory, or module name - whatever makes sense for the project. The "Lean Codebase Overview" section lets agents document the evolving structure as they build it out.

## CLI Commands

```bash
# Run the main loop (auto-initializes, loads manifest, resumes if interrupted)
repoprover run <path> [--clean]

# Show project status
repoprover status <path> [-c/--chapters/--units] [--prs]

# Export state as JSON
repoprover export <path> [-o output.json]

# Turn a paper into a project (arXiv id or local tex)
repoprover init-paper <path> (--arxiv <id> | --source <file|dir|tar.gz>)
```

**Notes:**
- `<path>` defaults to `.` (current directory) if not specified
- `--clean` starts from scratch: removes all Lean files (keeps tex), reinitializes git repo with a single initial commit, wipes worktrees and state
- The manifest file is auto-discovered: `manifest.json`, `repoprover.json`, or `.repoprover/manifest.json`

## Git Worktrees

Each agent works in an isolated git worktree:
- Separate branch per agent (e.g., `sketch-ch1-abc123`)
- Shared Mathlib via symlinks (~6.5GB saved per agent)
- Changes are committed to the agent's branch
- Review and merge happen via git operations

This allows multiple agents to work in parallel without conflicts.

## Review System

Every PR goes through two reviews:

**Math Review** (LLM)
- Proof completeness (no `sorry` in submitted proofs)
- Mathematical correctness
- Proper use of Mathlib

**Engineering Review** (LLM)
- Code style and formatting
- Naming conventions
- Documentation

Both must approve. If either requests changes, the agent revises and resubmits.

## Configuration

The `.repoprover/state.json` file is created automatically on first run. Key settings in `CoordinatorConfig`:

| Setting | Default | Description |
|---------|---------|-------------|
| `max_revisions` | 16 | Max revision attempts before giving up |
| `poll_interval` | 1.0 | Seconds between loop iterations |

## Requirements

- Python 3.10+
- Lean 4 with Lake
- An LLM endpoint (see [LLM endpoint](#llm-endpoint) below)

## LLM endpoint

By default every agent calls **`kimi-k3` on the comet gateway** (the self-hosted
SGLang fleet) — no API key needed. RepoProver owns its tool loop (it sends
OpenAI-style `tools` itself), so no external agent harness is involved.

- **Base URL**: read from `gateway_url` in the gateway state file
  `~/shrd/sglang-serve/state/comet-gateway.json` (override the path with
  `REPOPROVER_COMET_GATEWAY_FILE`) plus `/v1`. It is re-read whenever an agent
  creates its client, so a moved gateway is picked up by new agents.
- **Request extras**: `reasoning_effort` (default `high`) and
  `chat_template_kwargs.preserve_thinking=true`; the model's
  `reasoning_content` is kept in the agent's message history.
- **Defaults**: `max_tokens=32768`, `temperature=0.7`, 1800 s request timeout.
- **Startup check**: `run` lists the endpoint's models and fails fast if the
  model is not served (`--skip-llm-check` disables this).

`run` flags: `--provider {comet,anthropic,openai,google}`, `--model`,
`--base-url`, `--reasoning-effort {low,high,max}`, `--max-tokens`,
`--temperature`. The `stool` launcher forwards `--provider`, `--model`,
`--base_url`, `--reasoning_effort`. Non-comet providers need their key in
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY`; comet uses
`COMET_API_KEY` only if set.
- Git
