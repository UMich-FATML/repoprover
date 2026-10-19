#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""Repo-specific lint rules for RepoProver.

These are the invariants ruff and pyright cannot check: the docs layer stays
navigable, plans declare their status, manifests stay loadable, prompt
templates only use documented placeholders, and oversized files are a
recorded decision. Every error says how to fix it.

Run via `pixi run lint` (ruff runs first, then this script).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from string import Formatter

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Rules data (keep in sync with docs/design.md and the prompt templates)
# ---------------------------------------------------------------------------

AGENTS_MD_MAX_LINES = 150
LARGE_FILE_MAX_LINES = 1500

# Files whose size is a recorded decision in docs/tech-debt.md
GENERATED_FILES = {"pixi.lock"}  # never counted, never linted

# The placeholders each prompt template is allowed to use. A stray brace or a
# typo'd field only breaks at agent runtime, so it is linted here instead.
PROMPT_PLACEHOLDERS: dict[str, set[str]] = {
    "CONTRIBUTOR_BASE_PROMPT": {"project_noun"},
    "SKETCH_MODE_PROMPT": {"chapter_id", "source_tex_path", "lean_path", "unit_noun", "unit_noun_cap"},
    "PAPER_SKETCH_GUIDANCE": set(),
    "PROVE_MODE_PROMPT": {"chapter_id", "theorem_name", "lean_path", "source_tex_path", "unit_noun", "unit_noun_cap"},
    "MAINTAIN_MODE_PROMPT": {"issue_id"},
    "SCAN_MODE_PROMPT": set(),
    "TRIAGE_MODE_PROMPT": set(),
    "PROGRESS_MODE_PROMPT": {"unit_noun"},
    "COMPACTION_PROMPT_TEMPLATE": {"remaining_tokens_k"},
}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def error(msg: str) -> str:
    return msg


# ---------------------------------------------------------------------------
# Checks (each returns a list of "how to fix" error strings)
# ---------------------------------------------------------------------------


def check_agents_md_size() -> list[str]:
    """AGENTS.md is the repo map: it must stay readable in one screen."""
    path = REPO_ROOT / "AGENTS.md"
    if not path.exists():
        return ["AGENTS.md is missing — create it (see docs/plans/2026-10-19-harness-setup.md)"]
    n = len(path.read_text(encoding="utf-8").splitlines())
    if n > AGENTS_MD_MAX_LINES:
        return [
            f"AGENTS.md is {n} lines (max {AGENTS_MD_MAX_LINES}). Move detail into docs/ and leave the map; "
            f"docs/ is the system of record (see AGENTS.md 'Docs are the system of record')."
        ]
    return []


def markdown_files() -> list[Path]:
    files = [
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "README.md",
        REPO_ROOT / "CONTRIBUTING.md",
    ]
    files.extend(sorted((REPO_ROOT / "docs").rglob("*.md")))
    return [f for f in files if f.exists()]


def check_doc_links() -> list[str]:
    """Relative links in the docs layer must resolve (anchors ignored)."""
    errors: list[str] = []
    for doc in markdown_files():
        text = doc.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            if target.startswith("#") or EXTERNAL_RE.match(target) or target.startswith("<"):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (doc.parent / path_part).resolve()
            if not resolved.exists():
                errors.append(
                    f"{doc.relative_to(REPO_ROOT)}: link '{target}' does not resolve. "
                    f"Fix the path (relative to {doc.parent.relative_to(REPO_ROOT) or '.'}), "
                    f"or create the file, or drop the link."
                )
    return errors


def check_plans_status() -> list[str]:
    """Every plan declares where it stands, so stale plans are visible."""
    plans_dir = REPO_ROOT / "docs" / "plans"
    if not plans_dir.exists():
        return []
    errors: list[str] = []
    for plan in sorted(plans_dir.glob("*.md")):
        head = "\n".join(plan.read_text(encoding="utf-8").splitlines()[:10])
        if not re.search(r"^Status:", head, re.MULTILINE):
            errors.append(
                f"{plan.relative_to(REPO_ROOT)}: no 'Status:' line in the first 10 lines. "
                f"Add one, e.g. 'Status: In progress' / 'Status: Complete (date)' / 'Status: Abandoned (why)'."
            )
    return errors


def check_manifests_load() -> list[str]:
    """Every checked-in manifest must load through the one true loader."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from repoprover.manifest import ManifestError, load_manifest

    errors: list[str] = []
    patterns = ["examples/**/manifest.json", "configs/**/manifest.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(REPO_ROOT.glob(pattern)))
    if not paths:
        errors.append("No manifest.json found under examples/ or configs/ — the lint cannot check the schema.")
    for path in paths:
        try:
            load_manifest(path)
        except ManifestError as e:
            errors.append(
                f"{path.relative_to(REPO_ROOT)} does not load: {e} "
                f"Fix the manifest (schema: docs/sources.md) — do not hand-parse it downstream."
            )
    return errors


def check_prompt_placeholders() -> list[str]:
    """Prompt templates may only use documented placeholders."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from repoprover.agents import contributor, tools

    errors: list[str] = []
    for name, allowed in PROMPT_PLACEHOLDERS.items():
        module = tools if name == "COMPACTION_PROMPT_TEMPLATE" else contributor
        template = getattr(module, name, None)
        if template is None:
            errors.append(
                f"scripts/lint.py expects {module.__name__}.{name} but it is gone. "
                f"Remove it from PROMPT_PLACEHOLDERS here (or fix the name)."
            )
            continue
        used = {f for _, f, _, _ in Formatter().parse(template) if f is not None}
        if used != allowed:
            errors.append(
                f"{module.__name__}.{name}: placeholders {sorted(used)} != documented {sorted(allowed)}. "
                f"Update the template or PROMPT_PLACEHOLDERS here AND tests/test_contributor_prompts.py "
                f"(both lists must match; a mismatch means a KeyError at agent runtime)."
            )
    return errors


def tracked_text_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    files = []
    for line in result.stdout.splitlines():
        path = REPO_ROOT / line
        if not path.is_file() or path.name in GENERATED_FILES:
            continue
        if path.suffix in {".py", ".html", ".js", ".md", ".yaml", ".yml", ".toml", ".sh"}:
            files.append(path)
    return files


def check_file_sizes() -> list[str]:
    """Files >1500 lines are allowed only as a recorded decision."""
    tech_debt = REPO_ROOT / "docs" / "tech-debt.md"
    debt_text = tech_debt.read_text(encoding="utf-8") if tech_debt.exists() else ""
    errors: list[str] = []
    for path in tracked_text_files():
        n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if n <= LARGE_FILE_MAX_LINES:
            continue
        rel = path.relative_to(REPO_ROOT)
        if str(rel) in debt_text or path.name in debt_text:
            continue  # grandfathered: listed in docs/tech-debt.md
        errors.append(
            f"{rel} is {n} lines (max {LARGE_FILE_MAX_LINES}). Split it, or add a row for it to the "
            f"'Oversized files' table in docs/tech-debt.md with why it is safe to keep."
        )
    return errors


CHECKS = [
    ("AGENTS.md size", check_agents_md_size),
    ("doc links resolve", check_doc_links),
    ("plans have Status:", check_plans_status),
    ("manifests load", check_manifests_load),
    ("prompt placeholders documented", check_prompt_placeholders),
    ("file sizes recorded", check_file_sizes),
]


def main() -> int:
    failures = 0
    for label, check in CHECKS:
        errors = []
        try:
            errors = check()
        except Exception as e:  # a broken lint must fail loudly, not silently pass
            errors = [f"{label}: lint crashed: {e!r} — this is a bug in scripts/lint.py, fix the lint"]
        for msg in errors:
            failures += 1
            print(f"error: {msg}")
        if not errors:
            print(f"ok: {label}")
    if failures:
        print(f"\n{failures} error(s). Each message above says how to fix it.")
        return 1
    print("\nAll repo lints passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
