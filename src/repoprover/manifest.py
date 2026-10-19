# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""Manifest loading, validation, and derived artifacts.

The manifest maps a source (a book or a paper) to the units of formalization
work: one unit per tex file (a book chapter or a paper section). This module
is the single owner of that schema. The coordinator, the `init-paper` command
and `scripts/lint.py` all go through it, so a manifest is validated in exactly
one place. The format is documented in docs/sources.md.

Terminology (see docs/design.md):
- "unit" is the generic term for a chapter (book) or section (paper).
- Internally a unit id is called `chapter_id` for historical reasons; state
  files, recordings and the viewer all use that key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

MANIFEST_DOC = "docs/sources.md#manifest"

# Accepted top-level keys for the unit list. "chapters" is canonical for
# books, "sections" for papers; both are accepted for either kind so
# hand-edited manifests keep working.
_UNIT_KEYS = ("chapters", "sections")

# Legacy alias for source_path found in older manifests.
_SOURCE_KEYS = ("source_path", "source")


class ManifestError(ValueError):
    """Raised when a manifest is invalid.

    The message always says what is wrong and where the format is documented,
    so an agent can repair the manifest from the error alone.
    """


class SourceKind(StrEnum):
    """What kind of source a project formalizes.

    The kind only changes wording (prompts, CLI output, docs) - the run loop,
    review flow and state format are identical for books and papers.
    """

    BOOK = "book"
    PAPER = "paper"

    @property
    def project_noun(self) -> str:
        """ "book" or "paper" - for prose that names the whole source."""
        return "book" if self is SourceKind.BOOK else "paper"

    @property
    def unit_noun(self) -> str:
        """ "chapter" or "section" - for prose that names one unit."""
        return "chapter" if self is SourceKind.BOOK else "section"

    @property
    def unit_noun_cap(self) -> str:
        """ "Chapter" or "Section" - for prompt and table headings."""
        return self.unit_noun.capitalize()

    @property
    def unit_noun_plural(self) -> str:
        """ "chapters" or "sections"."""
        return "chapters" if self is SourceKind.BOOK else "sections"

    @classmethod
    def parse(cls, value: str, *, where: str = "manifest") -> SourceKind:
        """Parse a kind string, raising ManifestError with a fix hint on bad input."""
        try:
            return cls(value)
        except ValueError:
            valid = ", ".join(repr(k.value) for k in cls)
            raise ManifestError(f'{where}: "kind" is {value!r}, expected one of: {valid}. See {MANIFEST_DOC}') from None


@dataclass
class Unit:
    """One unit of formalization work: a tex source and its target theorems."""

    id: str
    source_path: str
    title: str = ""  # Defaults to the id
    lean_path: str = ""  # Optional target Lean file
    target_theorems: list[str] = field(default_factory=list)  # Theorem labels that define "done"

    def to_dict(self) -> dict:
        d: dict = {"id": self.id}
        if self.title:
            d["title"] = self.title
        d["source_path"] = self.source_path
        if self.lean_path:
            d["lean_path"] = self.lean_path
        if self.target_theorems:
            d["target_theorems"] = list(self.target_theorems)
        return d


@dataclass
class Manifest:
    """A validated manifest: source kind, title, and the units to formalize."""

    kind: SourceKind = SourceKind.BOOK
    title: str = ""
    units: list[Unit] = field(default_factory=list)
    arxiv_id: str | None = None
    source_url: str | None = None

    @property
    def unit_key(self) -> str:
        """Top-level key under which units are written ("chapters" or "sections")."""
        return "chapters" if self.kind is SourceKind.BOOK else "sections"

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind.value}
        if self.title:
            d["title"] = self.title
        if self.arxiv_id:
            d["arxiv_id"] = self.arxiv_id
        if self.source_url:
            d["source_url"] = self.source_url
        d[self.unit_key] = [u.to_dict() for u in self.units]
        return d

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def _require_str(value: object, *, key: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f'{where}: "{key}" must be a non-empty string, got {value!r}. See {MANIFEST_DOC}')
    return value


def load_manifest(path: Path) -> Manifest:
    """Load and validate a manifest file.

    Raises ManifestError on any problem; the message names the offending unit
    and points at docs/sources.md.
    """
    name = Path(path).name
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"{name}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}") from e
    except OSError as e:
        raise ManifestError(f"{name}: cannot read manifest: {e}") from e

    if not isinstance(data, dict):
        raise ManifestError(f"{name}: top level must be a JSON object, got {type(data).__name__}. See {MANIFEST_DOC}")

    kind = SourceKind.parse(str(data.get("kind", SourceKind.BOOK.value)), where=name)
    title = data.get("title", "")
    if not isinstance(title, str):
        raise ManifestError(f'{name}: "title" must be a string, got {title!r}. See {MANIFEST_DOC}')
    arxiv_id = data.get("arxiv_id")
    source_url = data.get("source_url")
    for key, value in (("arxiv_id", arxiv_id), ("source_url", source_url)):
        if value is not None and not isinstance(value, str):
            raise ManifestError(f'{name}: "{key}" must be a string, got {value!r}. See {MANIFEST_DOC}')

    present = [k for k in _UNIT_KEYS if k in data]
    if not present:
        raise ManifestError(
            f'{name}: no unit list found. Add a "chapters" (book) or "sections" (paper) array. See {MANIFEST_DOC}'
        )
    if len(present) > 1:
        raise ManifestError(
            f"{name}: both {present[0]!r} and {present[1]!r} are present; keep exactly one "
            f'("chapters" for books, "sections" for papers). See {MANIFEST_DOC}'
        )
    raw_units = data[present[0]]
    if not isinstance(raw_units, list):
        raise ManifestError(
            f'{name}: "{present[0]}" must be an array of unit objects, got {type(raw_units).__name__}. '
            f"See {MANIFEST_DOC}"
        )

    units: list[Unit] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(raw_units):
        where = f"{name}: unit #{i + 1}"
        if not isinstance(raw, dict):
            raise ManifestError(f"{where} must be an object, got {type(raw).__name__}. See {MANIFEST_DOC}")
        unit_id = _require_str(raw.get("id"), key="id", where=where)

        source = None
        for key in _SOURCE_KEYS:
            if key in raw:
                source = _require_str(raw[key], key=key, where=where)
                break
        if source is None:
            raise ManifestError(
                f'{where} ({unit_id!r}) has no "source_path". Each unit needs "id" and '
                f'"source_path" (a repo-relative path to its tex file). See {MANIFEST_DOC}'
            )
        if Path(source).is_absolute() or ".." in Path(source).parts:
            raise ManifestError(
                f'{where} ({unit_id!r}): "source_path" {source!r} must be relative to the '
                f"project root and stay inside it. See {MANIFEST_DOC}"
            )

        if unit_id in seen_ids:
            raise ManifestError(f"{where}: duplicate unit id {unit_id!r}. Unit ids must be unique. See {MANIFEST_DOC}")
        seen_ids.add(unit_id)

        title_value = raw.get("title", "")
        if not isinstance(title_value, str):
            raise ManifestError(
                f'{where} ({unit_id!r}): "title" must be a string, got {title_value!r}. See {MANIFEST_DOC}'
            )
        lean_path = raw.get("lean_path", "")
        if not isinstance(lean_path, str):
            raise ManifestError(
                f'{where} ({unit_id!r}): "lean_path" must be a string, got {lean_path!r}. See {MANIFEST_DOC}'
            )
        target_theorems = raw.get("target_theorems", [])
        if not isinstance(target_theorems, list) or not all(isinstance(t, str) for t in target_theorems):
            raise ManifestError(
                f'{where} ({unit_id!r}): "target_theorems" must be a list of strings '
                f"(theorem labels). See {MANIFEST_DOC}"
            )

        units.append(
            Unit(
                id=unit_id,
                source_path=source,
                title=title_value or unit_id,
                lean_path=lean_path,
                target_theorems=list(target_theorems),
            )
        )

    return Manifest(kind=kind, title=title, units=units, arxiv_id=arxiv_id, source_url=source_url)


def find_manifest(base_project: Path) -> Path | None:
    """Find the manifest file in a project directory (manifest.json, repoprover.json, or .repoprover/manifest.json)."""
    candidates = [
        base_project / "manifest.json",
        base_project / "repoprover.json",
        base_project / ".repoprover" / "manifest.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def render_contents_md(manifest: Manifest) -> str:
    """Render the initial CONTENTS.md for a project.

    Agents keep this file updated as the Lean codebase evolves; this is only
    the generated starting point. The header uses the unit noun of the kind.
    """
    unit_header = manifest.kind.unit_noun.capitalize()
    lines = [
        "# Contents",
        "",
        "## TeX Sources",
        "",
        f"| {unit_header} | Source | Lean Entry Point | Comments |",
        "|---------|--------|------------------|----------|",
    ]
    for unit in manifest.units:
        lines.append(f"| {unit.title} | `{unit.source_path}` | | |")
    lines.extend(
        [
            "",
            "---",
            "",
            "## Lean Codebase Overview",
            "",
            "*(Document the structure of the Lean codebase here as it evolves:",
            "module hierarchy, shared utilities, naming conventions, etc.)*",
            "",
        ]
    )
    return "\n".join(lines)


def initial_sketch_issues(manifest: Manifest) -> list[dict]:
    """Issue payloads for sketching each unit (one per unit with a source).

    The origin string is a stable marker the coordinator matches on; do not
    change it without updating _launch_maintainers in coordinator.py.
    """
    return [
        {
            "status": "open",
            "origin": "initial chapter sketch",
            "description": f"[{unit.id}] formalize `{unit.source_path}` (TeX → Lean)",
        }
        for unit in manifest.units
        if unit.source_path
    ]
