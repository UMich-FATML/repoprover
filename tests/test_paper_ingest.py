# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""Tests for paper ingestion: fetch sniffing, flatten, split, targets.

No test touches the network: fetch_arxiv_source is injected where needed.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest

from repoprover.manifest import load_manifest
from repoprover.paper_ingest import (
    _ARXIV_ID_RE,
    IngestError,
    extract_targets,
    fetch_arxiv_source,
    find_main_file,
    flatten_tex,
    ingest_paper,
    parse_newtheorem_environments,
    parse_title,
    slugify,
    split_sections,
)

TOY_PAPER = Path(__file__).resolve().parent.parent / "examples" / "toy_paper"


# =============================================================================
# Fixtures: build e-print archives from the toy paper without the network
# =============================================================================


def make_tar_gz(dest: Path, files: dict[str, str]) -> Path:
    """Write a gzipped tar archive (like an arXiv e-print) into dest."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    dest.write_bytes(buf.getvalue())
    return dest


def toy_paper_files() -> dict[str, str]:
    return {
        "main.tex": (TOY_PAPER / "main.tex").read_text(),
        "intro-facts.tex": (TOY_PAPER / "intro-facts.tex").read_text(),
        "refs.bib": (TOY_PAPER / "refs.bib").read_text(),
    }


def fake_fetch(files: dict[str, str]):
    """A fetch_arxiv_source stand-in that writes a local e-print archive."""

    def _fetch(arxiv_id: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        return make_tar_gz(dest / f"{arxiv_id}.eprint", files)

    return _fetch


class TestFetchArxivId:
    def test_rejects_bad_id(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="does not look like an arXiv id"):
            fetch_arxiv_source("not an id", tmp_path)

    def test_accepts_versioned_and_legacy_ids(self, tmp_path: Path) -> None:
        # Only the id *shape* is validated without touching the network
        for good in ("2506.00738", "2506.00738v2", "math/0601001"):
            assert _ARXIV_ID_RE.match(good), good
        for bad in ("not-an-id", "2506.00738v", "http://arxiv.org/abs/2506.00738"):
            assert not _ARXIV_ID_RE.match(bad), bad


# =============================================================================
# Flatten / split / targets
# =============================================================================


class TestFlatten:
    def test_inlines_inputs_recursively(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\n\\input{a}\n\\end{document}\n")
        (tmp_path / "a.tex").write_text("A says \\input{sub/b}\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.tex").write_text("B content\n")
        flattened, warnings = flatten_tex(tmp_path / "main.tex")
        assert "A says B content" in flattened
        assert "\\input" not in flattened
        assert warnings == []

    def test_missing_input_left_in_place_with_warning(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\input{missing}\n")
        flattened, warnings = flatten_tex(tmp_path / "main.tex")
        assert "\\input{missing}" in flattened
        assert any("missing" in w for w in warnings)

    def test_cycle_is_contained(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("start \\input{a}")
        (tmp_path / "a.tex").write_text("A \\input{b}")
        (tmp_path / "b.tex").write_text("B \\input{a}")
        flattened, warnings = flatten_tex(tmp_path / "main.tex")
        assert "start A B" in flattened
        assert any("cycle" in w for w in warnings)

    def test_bbl_input_not_inlined(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("\\input{main.bbl}")
        (tmp_path / "main.bbl").write_text(" bibliography content ")
        flattened, _ = flatten_tex(tmp_path / "main.tex")
        assert "\\input{main.bbl}" in flattened

    def test_commented_directives_not_inlined(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("% \\include{ch2}\n  %\\input{ch2}\n\\input{ch1}\n")
        (tmp_path / "ch1.tex").write_text("CH1 body\n")
        (tmp_path / "ch2.tex").write_text("CH2 body\n")
        flattened, warnings = flatten_tex(tmp_path / "main.tex")
        assert "CH1 body" in flattened
        assert "CH2 body" not in flattened
        assert "% \\include{ch2}\n  %\\input{ch2}\n" in flattened
        assert warnings == []

    def test_trailing_comment_directive_not_inlined(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("\\input{a} % \\input{b}\n")
        (tmp_path / "a.tex").write_text("A")
        (tmp_path / "b.tex").write_text("B")
        flattened, _ = flatten_tex(tmp_path / "main.tex")
        assert flattened == "A % \\input{b}\n"

    def test_escaped_percent_is_not_a_comment(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("50\\% \\input{a}\n")
        (tmp_path / "a.tex").write_text("A")
        flattened, _ = flatten_tex(tmp_path / "main.tex")
        assert flattened == "50\\% A\n"

    def test_percent_after_line_break_is_a_comment(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("x\\\\% \\input{a}\n")
        (tmp_path / "a.tex").write_text("A")
        flattened, _ = flatten_tex(tmp_path / "main.tex")
        assert flattened == "x\\\\% \\input{a}\n"

    def test_commented_missing_input_no_warning(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("% \\input{missing}\n")
        _, warnings = flatten_tex(tmp_path / "main.tex")
        assert warnings == []


class TestMainFile:
    def test_finds_documentclass_file(self, tmp_path: Path) -> None:
        (tmp_path / "main.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}")
        (tmp_path / "other.tex").write_text("no documentclass here")
        assert find_main_file(tmp_path).name == "main.tex"

    def test_errors_when_missing(self, tmp_path: Path) -> None:
        (tmp_path / "other.tex").write_text("no documentclass")
        with pytest.raises(IngestError, match="--source <main.tex>"):
            find_main_file(tmp_path)


class TestTargets:
    def test_newtheorem_discovery(self) -> None:
        preamble = r"\newtheorem{theorem}{Theorem}" "\n" r"\newtheorem*{remark}{Remark}"
        assert parse_newtheorem_environments(preamble) == ["theorem", "remark"]

    def test_label_extraction_maps_colon_to_dot(self) -> None:
        body = (
            "\\begin{theorem}\\label{thm:main}statement\\end{theorem}\n"
            "\\begin{lemma}\\label{lem:aux}aux\\end{lemma}\n"
            "\\begin{corollary}no label\\end{corollary}\n"
        )
        labels, unlabeled = extract_targets(body, ["theorem", "lemma", "corollary"])
        assert labels == ["thm.main", "lem.aux"]
        assert unlabeled == 1

    def test_starred_environments_count(self) -> None:
        body = "\\begin{theorem*}\\label{t:star}x\\end{theorem*}"
        labels, unlabeled = extract_targets(body, ["theorem"])
        assert labels == ["t.star"] and unlabeled == 0


class TestSplit:
    def test_split_sections_full_paper(self) -> None:
        flattened = (
            (TOY_PAPER / "main.tex")
            .read_text()
            .replace("\\input{intro-facts}", (TOY_PAPER / "intro-facts.tex").read_text())
        )
        preamble, sections, unlabeled = split_sections(flattened, ["theorem", "lemma", "corollary"])
        assert "newtheorem" in preamble and "documentclass" in preamble
        ids = [s.id for s in sections]
        assert ids == [
            "front-matter",
            "introduction",
            "results",
            "appendix-background-on-order",
        ]
        assert sections[3].is_appendix
        by_id = {s.id: s for s in sections}
        assert by_id["introduction"].targets == ["lem.double-zero", "thm.double-strict"]
        assert by_id["results"].targets == [
            "thm.double-add",
            "cor.double-two",
            "lem.double-even",
        ]
        assert by_id["results"].unlabeled_results == 1
        assert by_id["appendix-background-on-order"].targets == ["lem.double-mono"]
        assert unlabeled == 1

    def test_subsection_not_split(self) -> None:
        flattened = (
            "\\documentclass{article}\\begin{document}\n"
            "\\section{One}\n\\subsection{Sub}\n\\section{Two}\n\\end{document}\n"
        )
        _, sections, _ = split_sections(flattened, [])
        assert [s.id for s in sections] == ["one", "two"]

    def test_commented_section_ignored(self) -> None:
        flattened = (
            "\\documentclass{article}\\begin{document}\n% \\section{Commented}\n\\section{Real}\n\\end{document}\n"
        )
        _, sections, _ = split_sections(flattened, [])
        assert [s.id for s in sections] == ["real"]

    def test_duplicate_titles_get_suffixes(self) -> None:
        flattened = (
            "\\documentclass{article}\\begin{document}\n\\section{Results}\n\\section{Results}\n\\end{document}\n"
        )
        _, sections, _ = split_sections(flattened, [])
        assert [s.id for s in sections] == ["results", "results-2"]

    def test_title_parse(self) -> None:
        flattened = "\\title{Doubling \\emph{Properties} of $N$\\thanks{Grant X}}\nrest"
        assert parse_title(flattened, "fallback") == "Doubling Properties of $N$"
        assert parse_title("no title here", "fallback") == "fallback"

    def test_slugify(self) -> None:
        assert slugify("Background on Order!") == "background-on-order"
        assert slugify("") == "section"


# =============================================================================
# End-to-end ingest_paper
# =============================================================================


class TestIngestPaper:
    def test_ingests_local_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        result = ingest_paper(project, source=TOY_PAPER, project_name="ToyPaper")
        assert result.manifest.kind.value == "paper"
        assert result.manifest.title == "Doubling Properties of the Natural Numbers"
        assert [u.id for u in result.manifest.units] == [
            "front-matter",
            "introduction",
            "results",
            "appendix-background-on-order",
        ]
        results_unit = next(u for u in result.manifest.units if u.id == "results")
        assert "thm.double-add" in results_unit.target_theorems

        manifest_file = project / "manifest.json"
        # Round-trips through the shared loader
        reloaded = load_manifest(manifest_file)
        assert [u.id for u in reloaded.units] == [u.id for u in result.manifest.units]

        # Written artifacts
        assert (project / "CONTENTS.md").exists()
        assert (project / "issues" / "README.md").exists()
        assert (project / "tex" / "preamble.tex").exists()
        assert (project / "tex" / "all.tex").exists()
        assert (project / "tex" / "02-results.tex").exists()
        # Bib copied for [cited] decisions
        assert (project / "tex" / "refs.bib").exists()
        # .gitignore keeps run state out
        assert ".repoprover/" in (project / ".gitignore").read_text()

    def test_ingests_from_arxiv_with_injected_fetch(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        result = ingest_paper(project, arxiv="2506.00738v2", fetch=fake_fetch(toy_paper_files()))
        assert result.manifest.arxiv_id == "2506.00738v2"
        data = json.loads((project / "manifest.json").read_text())
        assert data["kind"] == "paper"
        assert data["arxiv_id"] == "2506.00738v2"
        assert data["source_url"] == "https://arxiv.org/abs/2506.00738v2"
        assert "sections" in data and "chapters" not in data

    def test_pdf_only_paper_is_refused_and_writes_nothing(self, tmp_path: Path) -> None:
        def pdf_fetch(arxiv_id: str, dest: Path) -> Path:
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / "fake.eprint"
            path.write_bytes(b"%PDF-1.4 fake pdf content")
            return path

        project = tmp_path / "proj"
        with pytest.raises(IngestError, match="no LaTeX source"):
            ingest_paper(project, arxiv="2506.00738", fetch=pdf_fetch)
        assert not project.exists(), "a refused ingest must not create the project dir"

    def test_unknown_format_is_refused(self, tmp_path: Path) -> None:
        def ps_fetch(arxiv_id: str, dest: Path) -> Path:
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / "fake.eprint"
            path.write_bytes(b"%!PS-Adobe-3.0 postscript")
            return path

        with pytest.raises(IngestError, match="postscript"):
            ingest_paper(tmp_path / "proj", arxiv="2506.00738", fetch=ps_fetch)

    def test_single_gzipped_tex_source(self, tmp_path: Path) -> None:
        source = tmp_path / "paper.tex.gz"
        source.write_bytes(gzip.compress((TOY_PAPER / "main.tex").read_bytes()))
        project = tmp_path / "proj"
        result = ingest_paper(project, source=source)
        # intro-facts.tex is missing next to the single file: left in place, warned
        assert any("intro-facts" in w for w in result.warnings)
        assert (project / "tex" / "all.tex").exists()

    def test_tar_gz_source(self, tmp_path: Path) -> None:
        archive = make_tar_gz(tmp_path / "eprint.tar.gz", toy_paper_files())
        project = tmp_path / "proj"
        result = ingest_paper(project, source=archive)
        assert len(result.manifest.units) == 4

    def test_refuses_existing_project_without_force(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        ingest_paper(project, source=TOY_PAPER)
        with pytest.raises(IngestError, match="--force"):
            ingest_paper(project, source=TOY_PAPER)
        # With force it succeeds
        ingest_paper(project, source=TOY_PAPER, force=True)

    def test_split_none_single_unit(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        result = ingest_paper(project, source=TOY_PAPER, split="none")
        assert [u.id for u in result.manifest.units] == ["all"]
        all_unit = result.manifest.units[0]
        assert all_unit.source_path == "tex/all.tex"
        assert "thm.double-add" in all_unit.target_theorems
        # Section files are not written
        assert not (project / "tex" / "02-results.tex").exists()

    def test_requires_exactly_one_source(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="exactly one"):
            ingest_paper(tmp_path / "proj")
        with pytest.raises(IngestError, match="exactly one"):
            ingest_paper(tmp_path / "proj", arxiv="2506.00738", source=TOY_PAPER)

    def test_missing_source_path(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="does not exist"):
            ingest_paper(tmp_path / "proj", source=tmp_path / "nope.tex")

    def test_non_tex_source_file(self, tmp_path: Path) -> None:
        source = tmp_path / "paper.txt"
        source.write_text("not tex")
        with pytest.raises(IngestError, match="expected a .tex file"):
            ingest_paper(tmp_path / "proj", source=source)
