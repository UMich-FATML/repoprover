# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""Tests for the manifest module: validation, round-trips, derived artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repoprover.manifest import (
    MANIFEST_DOC,
    Manifest,
    ManifestError,
    SourceKind,
    Unit,
    find_manifest,
    initial_sketch_issues,
    load_manifest,
    render_contents_md,
)


def write_manifest(tmp_path: Path, data: dict | str) -> Path:
    path = tmp_path / "manifest.json"
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


BOOK_MANIFEST = {
    "kind": "book",
    "title": "My Book",
    "chapters": [
        {"id": "ch1", "title": "Introduction", "source_path": "tex/ch1.tex", "target_theorems": ["thm:main"]},
        {"id": "ch2", "source_path": "tex/ch2.tex"},
    ],
}

PAPER_MANIFEST = {
    "kind": "paper",
    "title": "A Paper",
    "arxiv_id": "2506.00738",
    "sections": [
        {"id": "intro", "title": "Introduction", "source_path": "tex/01-introduction.tex"},
    ],
}


class TestSourceKind:
    def test_nouns(self) -> None:
        book = SourceKind.BOOK
        paper = SourceKind.PAPER
        assert (book.project_noun, book.unit_noun, book.unit_noun_plural) == ("book", "chapter", "chapters")
        assert (paper.project_noun, paper.unit_noun, paper.unit_noun_plural) == ("paper", "section", "sections")

    def test_parse_rejects_unknown(self) -> None:
        with pytest.raises(ManifestError, match="kind.*See " + MANIFEST_DOC.replace(".", r"\.")):
            SourceKind.parse("pamphlet")


class TestLoadManifest:
    def test_loads_book_manifest(self, tmp_path: Path) -> None:
        m = load_manifest(write_manifest(tmp_path, BOOK_MANIFEST))
        assert m.kind is SourceKind.BOOK
        assert m.title == "My Book"
        assert [u.id for u in m.units] == ["ch1", "ch2"]
        assert m.units[0].target_theorems == ["thm:main"]
        # Missing title falls back to the id
        assert m.units[1].title == "ch2"

    def test_loads_paper_manifest_with_sections_key(self, tmp_path: Path) -> None:
        m = load_manifest(write_manifest(tmp_path, PAPER_MANIFEST))
        assert m.kind is SourceKind.PAPER
        assert m.arxiv_id == "2506.00738"
        assert m.units[0].source_path == "tex/01-introduction.tex"

    def test_kind_defaults_to_book(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "c", "source_path": "tex/c.tex"}]}
        assert load_manifest(write_manifest(tmp_path, data)).kind is SourceKind.BOOK

    def test_sections_key_accepted_for_book_kind(self, tmp_path: Path) -> None:
        data = {"kind": "book", "sections": [{"id": "c", "source_path": "tex/c.tex"}]}
        m = load_manifest(write_manifest(tmp_path, data))
        assert m.kind is SourceKind.BOOK and len(m.units) == 1

    def test_rejects_both_unit_keys(self, tmp_path: Path) -> None:
        data = {**BOOK_MANIFEST, "sections": []}
        with pytest.raises(ManifestError, match="both 'chapters' and 'sections'"):
            load_manifest(write_manifest(tmp_path, data))

    def test_rejects_no_unit_key(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match='"chapters" \\(book\\) or "sections" \\(paper\\)'):
            load_manifest(write_manifest(tmp_path, {"kind": "book"}))

    def test_rejects_bad_json(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match="invalid JSON at line 1"):
            load_manifest(write_manifest(tmp_path, "{not json"))

    def test_error_mentions_docs_for_missing_source_path(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "c1", "source_path": "tex/c1.tex"}, {"id": "c2"}]}
        with pytest.raises(ManifestError, match=r'unit #2.*"source_path".*' + MANIFEST_DOC.replace(".", r"\.")):
            load_manifest(write_manifest(tmp_path, data))

    def test_accepts_legacy_source_key(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "c", "source": "tex/c.tex"}]}
        m = load_manifest(write_manifest(tmp_path, data))
        assert m.units[0].source_path == "tex/c.tex"

    def test_rejects_duplicate_ids(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "c", "source_path": "tex/a.tex"}, {"id": "c", "source_path": "tex/b.tex"}]}
        with pytest.raises(ManifestError, match="duplicate unit id 'c'"):
            load_manifest(write_manifest(tmp_path, data))

    def test_rejects_absolute_source_path(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "c", "source_path": "/etc/passwd"}]}
        with pytest.raises(ManifestError, match="relative to the project root"):
            load_manifest(write_manifest(tmp_path, data))

    def test_rejects_escaping_source_path(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "c", "source_path": "../outside.tex"}]}
        with pytest.raises(ManifestError, match="relative to the project root"):
            load_manifest(write_manifest(tmp_path, data))

    def test_rejects_bad_target_theorems(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "c", "source_path": "tex/c.tex", "target_theorems": "thm:main"}]}
        with pytest.raises(ManifestError, match='"target_theorems" must be a list of strings'):
            load_manifest(write_manifest(tmp_path, data))

    def test_rejects_empty_id(self, tmp_path: Path) -> None:
        data = {"chapters": [{"id": "", "source_path": "tex/c.tex"}]}
        with pytest.raises(ManifestError, match='"id" must be a non-empty string'):
            load_manifest(write_manifest(tmp_path, data))


class TestRoundTrip:
    def test_book_round_trip(self, tmp_path: Path) -> None:
        path = write_manifest(tmp_path, BOOK_MANIFEST)
        m1 = load_manifest(path)
        m1.save(path)
        m2 = load_manifest(path)
        assert m1.to_dict() == m2.to_dict()
        assert m2.to_dict()["chapters"][0]["target_theorems"] == ["thm:main"]

    def test_paper_writes_sections_key(self, tmp_path: Path) -> None:
        m = load_manifest(write_manifest(tmp_path, PAPER_MANIFEST))
        d = m.to_dict()
        assert "sections" in d and "chapters" not in d
        assert d["arxiv_id"] == "2506.00738"

    def test_book_writes_chapters_key(self, tmp_path: Path) -> None:
        m = load_manifest(write_manifest(tmp_path, BOOK_MANIFEST))
        d = m.to_dict()
        assert "chapters" in d and "sections" not in d

    def test_unit_dict_shape(self) -> None:
        u = Unit(id="c", source_path="tex/c.tex", title="C", target_theorems=["t"])
        assert u.to_dict() == {"id": "c", "title": "C", "source_path": "tex/c.tex", "target_theorems": ["t"]}
        assert Unit(id="c", source_path="tex/c.tex").to_dict() == {"id": "c", "source_path": "tex/c.tex"}


class TestDerivedArtifacts:
    def test_render_contents_md_uses_unit_noun(self) -> None:
        m = Manifest(
            kind=SourceKind.BOOK, title="My Book", units=[Unit(id="ch1", source_path="tex/ch1.tex", title="Intro")]
        )
        md = render_contents_md(m)
        assert "| Chapter | Source | Lean Entry Point | Comments |" in md
        assert "| Intro | `tex/ch1.tex` | | |" in md
        assert "Lean Codebase Overview" in md

        m_paper = Manifest(kind=SourceKind.PAPER, units=[Unit(id="intro", source_path="tex/01-intro.tex")])
        assert "| Section | Source | Lean Entry Point | Comments |" in render_contents_md(m_paper)

    def test_initial_sketch_issues(self) -> None:
        m = Manifest(
            kind=SourceKind.PAPER,
            units=[
                Unit(id="intro", source_path="tex/01-intro.tex"),
                Unit(id="app", source_path=""),  # no source -> no issue
            ],
        )
        issues = initial_sketch_issues(m)
        assert len(issues) == 1
        assert issues[0]["origin"] == "initial chapter sketch"  # stable marker, do not rename
        assert issues[0]["status"] == "open"
        assert "[intro]" in issues[0]["description"]
        assert "tex/01-intro.tex" in issues[0]["description"]


class TestFindManifest:
    def test_finds_manifest_json(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text("{}")
        assert find_manifest(tmp_path) == tmp_path / "manifest.json"

    def test_finds_repoprover_json(self, tmp_path: Path) -> None:
        (tmp_path / "repoprover.json").write_text("{}")
        assert find_manifest(tmp_path) == tmp_path / "repoprover.json"

    def test_finds_dotted_manifest(self, tmp_path: Path) -> None:
        (tmp_path / ".repoprover").mkdir()
        (tmp_path / ".repoprover" / "manifest.json").write_text("{}")
        assert find_manifest(tmp_path) == tmp_path / ".repoprover" / "manifest.json"

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert find_manifest(tmp_path) is None
