"""Mechanical resolution of import-only merge conflicts.

Every sketch adds `import Book.Chapter` to the root module, so two sketches
merged in a row always conflict there, and the coordinator used to bounce the
second PR back to its agent for a full revision. A conflict hunk in the
import header where at least one side holds only `import` lines (or
blanks/comments) has one sane resolution: every import from both sides, then
the other side's non-import lines (e.g. a module docstring added on `main`).
This module does that and nothing else; any other hunk leaves the file to
the agent.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_IMPORT_RE = re.compile(r"^\s*(import\s+\S.*|--.*|)$")


def _is_import_only(lines: list[str]) -> bool:
    return all(_IMPORT_RE.match(line) for line in lines)


def resolve_import_conflicts(text: str) -> str | None:
    """Resolve every conflict hunk in `text` as a union of import lines.

    Handles both `merge` and `diff3`/`zdiff3` conflict styles (the base
    section is dropped). Returns the resolved text, or None if the text has
    no conflict markers or any hunk is not import-only.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    found = False
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            out.append(lines[i])
            i += 1
            continue
        found = True
        ours: list[str] = []
        theirs: list[str] = []
        section = ours
        i += 1
        while i < len(lines) and not lines[i].startswith(">>>>>>> "):
            line = lines[i]
            if line.startswith("||||||| "):
                section = []  # base: dropped
            elif line.rstrip("\r\n") == "=======":
                section = theirs
            else:
                section.append(line)
            i += 1
        if i == len(lines):
            return None  # unterminated hunk
        i += 1  # skip >>>>>>>
        ours_pure = _is_import_only([ln.rstrip("\r\n") for ln in ours])
        theirs_pure = _is_import_only([ln.rstrip("\r\n") for ln in theirs])
        if not (ours_pure or theirs_pure):
            return None
        # Lean needs every import before any other command, so the hunk must sit in the import header.
        if not _is_import_only([ln.rstrip("\r\n") for ln in out]):
            return None
        rest_side = [] if ours_pure and theirs_pure else (theirs if ours_pure else ours)
        imports = [ln for ln in ours + theirs if ln.lstrip().startswith("import ")]
        rest = [ln for ln in rest_side if not ln.lstrip().startswith("import ")]
        seen: set[str] = set()
        for ln in imports:
            if ln.strip() in seen:
                continue
            seen.add(ln.strip())
            out.append(ln if ln.endswith("\n") else ln + "\n")
        out.extend(ln if ln.endswith("\n") else ln + "\n" for ln in rest)
    return "".join(out) if found else None


def auto_resolve_merge(repo: Path, conflict_files: list[str]) -> list[str]:
    """Resolve import-only conflicts in an in-progress merge in `repo`.

    Rewrites and stages each file `resolve_import_conflicts` can resolve.
    If every conflicted file was resolved, commits the merge and returns [];
    otherwise returns the files still in conflict (the caller aborts).
    """
    remaining: list[str] = []
    for rel in conflict_files:
        path = repo / rel
        try:
            resolved = resolve_import_conflicts(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            resolved = None  # deleted on one side, binary, ...
        if resolved is None:
            remaining.append(rel)
            continue
        path.write_text(resolved, encoding="utf-8")
        subprocess.run(["git", "add", "--", rel], cwd=repo, capture_output=True, check=True)
    if remaining:
        return remaining
    commit = subprocess.run(["git", "commit", "--no-edit"], cwd=repo, capture_output=True, text=True)
    if commit.returncode:
        logger.warning("auto-resolve: commit failed in %s: %s", repo, commit.stderr.strip())
        return conflict_files
    logger.info("auto-resolve: merged import-only conflicts in %s: %s", repo, ", ".join(conflict_files))
    return []
