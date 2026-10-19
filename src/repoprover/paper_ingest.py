# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

r"""Turn a research paper into a RepoProver project.

`repoprover init-paper` takes an arXiv id (or a local tex file / directory /
.tar.gz) and writes the files a RepoProver run needs:

    <project>/manifest.json     units (sections) + target theorem labels
    <project>/CONTENTS.md       generated table of contents
    <project>/tex/preamble.tex  everything before \begin{document}
    <project>/tex/all.tex       the whole flattened body, for reference
    <project>/tex/NN-<slug>.tex one file per top-level \section
    <project>/issues/           empty issue tracker (+ README)

The command never runs lake or git; it ends by printing the remaining setup
steps. The format it writes is documented in docs/sources.md.

Papers without LaTeX source (PDF-only e-prints) are refused, not OCR'd:
math OCR noise would silently poison sketchers and reviewers downstream,
and a clean refusal with the `--source` remedy is the safer failure (see
docs/design.md).
"""

from __future__ import annotations

import gzip
import re
import tarfile
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path

from .manifest import Manifest, SourceKind, Unit, render_contents_md

logger = getLogger(__name__)

ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{arxiv_id}"
ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
USER_AGENT = "repoprover/0.1 (paper ingestion; contact: repo maintainers)"

# Environments counted as "results" when the preamble declares no \newtheorem.
DEFAULT_RESULT_ENVIRONMENTS = ["theorem", "proposition", "lemma", "corollary", "definition", "conjecture"]

# Lines starting with % (after whitespace) are comments; never split on them.
_COMMENT_LINE_RE = re.compile(r"^\s*%")

# \section{Title} / \section*{Title} / \section[short]{Title}, but not \subsection
_SECTION_RE = re.compile(r"(?<![a-zA-Z@])\\section\*?\s*(?:\[[^\]]*\])?\s*\{")

_APPENDIX_RE = re.compile(r"^\s*\\appendix\b", re.MULTILINE)

_NEWTHEOREM_RE = re.compile(r"\\newtheorem\*?\s*\{([^}]+)\}")

# \input{x} / \include{x} / \subfile{x} to inline while flattening
_INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")

# A % starts a comment unless escaped: it must follow an even number of
# backslashes (`\%` is a literal percent; `\\%` is a line break, then a comment).
_COMMENT_START_RE = re.compile(r"(?<!\\)(?:\\\\)*%")

_ARXIV_ID_RE = re.compile(r"^[a-z\-]+/\d{7}$|^\d{4}\.\d{4,5}(v\d+)?$")

# Python >= 3.10.12 / 3.11.4 support the tar extraction filter; fall back to
# the manually validated plain extract on older patch releases.
try:
    tarfile.data_filter  # noqa: B018 - attribute probe
    _TAR_FILTER_SUPPORTED = True
except AttributeError:
    _TAR_FILTER_SUPPORTED = False


class IngestError(ValueError):
    """Raised when a paper cannot be ingested.

    The message always states the remedy (usually `--source` with a local
    tex file), so an agent or user can proceed from the error alone.
    """


@dataclass
class PaperSection:
    r"""One top-level \section of the paper."""

    title: str
    body: str  # tex content, starting with the \section line
    file_name: str  # e.g. "02-results.tex"
    id: str  # e.g. "results" or "appendix-proofs"
    is_appendix: bool = False
    targets: list[str] = field(default_factory=list)  # labels of labeled results
    unlabeled_results: int = 0  # results without a \label


@dataclass
class IngestResult:
    """What ingest_paper produced, for callers and tests."""

    manifest: Manifest
    sections: list[PaperSection]
    unlabeled_results: int
    warnings: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    title: str = ""


# =============================================================================
# Fetching
# =============================================================================


def fetch_arxiv_source(arxiv_id: str, dest: Path) -> Path:
    """Download the LaTeX source e-print for an arXiv id into dest.

    Returns the path of the downloaded file. Raises IngestError on network
    problems, with the `--source` remedy in the message. Injectable so tests
    can mock the network.
    """
    if not _ARXIV_ID_RE.match(arxiv_id.strip()):
        raise IngestError(
            f"{arxiv_id!r} does not look like an arXiv id (expected e.g. 2506.00738, "
            f"2506.00738v2 or math/0601001). Check the id, or use --source with a local tex file."
        )
    url = ARXIV_EPRINT_URL.format(arxiv_id=arxiv_id.strip())
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{arxiv_id.strip().replace('/', '_')}.eprint"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        raise IngestError(
            f"Could not download {url}: {e}. Download the paper's tex yourself and rerun "
            f"with --source <file.tex|dir|tar.gz>."
        ) from e
    logger.info("Downloaded %s (%d bytes)", url, target.stat().st_size)
    return target


def _detect_format(path: Path) -> str:
    """Sniff the container format of a downloaded e-print by magic bytes."""
    with open(path, "rb") as f:
        head = f.read(512)
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"%PDF"):
        return "pdf"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    if head.startswith(b"%!PS"):
        return "postscript"
    return "unknown"


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball, refusing members that would escape dest."""
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not target.is_relative_to(dest_resolved):
            raise IngestError(f"Archive member {member.name!r} escapes the extraction directory; refusing to extract.")
        if member.issym() or member.islnk():
            link_target = ((dest / member.name).parent / member.linkname).resolve()
            if not link_target.is_relative_to(dest_resolved):
                raise IngestError(
                    f"Archive member {member.name!r} links outside the extraction directory; refusing to extract."
                )
    if _TAR_FILTER_SUPPORTED:
        tf.extractall(dest, filter="data")
    else:  # member validation above stands in for the filter on old patch releases
        tf.extractall(dest)


def unpack_eprint(path: Path, dest: Path) -> Path:
    """Unpack a downloaded e-print into dest; returns the directory holding the tex tree.

    Nothing is written if the e-print is not a LaTeX source (PDF-only papers
    land here and raise).
    """
    fmt = _detect_format(path)
    if fmt == "pdf":
        raise IngestError(
            f"{path.name} is a PDF: this paper has no LaTeX source on arXiv "
            f"(PDF-only submission or scanned older paper). RepoProver does not OCR PDFs - "
            f"the OCR noise would produce wrong formalizations that reviews cannot catch. "
            f"Get the authors' tex (project page, personal site, or email) and rerun with "
            f"--source <file.tex|dir|tar.gz>. See docs/sources.md."
        )
    if fmt in ("postscript", "unknown"):
        raise IngestError(
            f"{path.name} is not a LaTeX source archive (detected: {fmt}). "
            f"Get the paper's tex and rerun with --source <file.tex|dir|tar.gz>."
        )

    dest.mkdir(parents=True, exist_ok=True)
    if fmt == "tar":
        with tarfile.open(path, "r:") as tf:
            _safe_extract(tf, dest)
        return dest

    # gzip: either a gzipped tarball or a single gzipped tex file
    try:
        with tarfile.open(path, "r:gz") as tf:
            _safe_extract(tf, dest)
        return dest
    except tarfile.ReadError:
        pass

    # Single gzipped file
    try:
        content = gzip.open(path, "rb").read()
    except OSError as e:
        raise IngestError(f"{path.name} is neither a tarball nor a gzipped tex file: {e}") from e
    if content.startswith(b"%PDF"):
        raise IngestError(
            f"{path.name} is a gzipped PDF: this paper has no LaTeX source on arXiv. "
            f"Get the authors' tex and rerun with --source <file.tex|dir|tar.gz>. See docs/sources.md."
        )
    if b"\x00" in content[:4096]:
        raise IngestError(
            f"{path.name} does not look like a tex file (binary content). "
            f"Rerun with --source pointing at the paper's tex."
        )
    stem = path.name
    for suffix in (".tar.gz", ".tgz", ".gz", ".eprint"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    tex_path = dest / f"{stem or 'paper'}.tex"
    tex_path.write_bytes(content)
    return dest


# =============================================================================
# LaTeX processing
# =============================================================================


def _extract_braced(text: str, start: int) -> tuple[str, int]:
    """Extract the brace group at text[start] == '{'. Returns (content, index after ']')."""
    if start >= len(text) or text[start] != "{":
        raise IngestError(f"expected '{{' at position {start} in LaTeX source")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
    raise IngestError("unbalanced braces in LaTeX source")


def find_main_file(tex_root: Path) -> Path:
    """Find the main .tex file (has \\documentclass and \\begin{document})."""
    candidates: list[Path] = []
    for tex_file in sorted(tex_root.rglob("*.tex")):
        try:
            content = tex_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\\documentclass" in content and "\\begin{document}" in content:
            candidates.append(tex_file)
    if not candidates:
        raise IngestError(
            f"No .tex file under {tex_root} has both \\documentclass and \\begin{{document}}. "
            f"Pass the main file directly: --source <main.tex>."
        )
    if len(candidates) == 1:
        return candidates[0]
    for preferred in ("main.tex", "paper.tex", "ms.tex"):
        for candidate in candidates:
            if candidate.name == preferred:
                return candidate
    listing = ", ".join(str(c.relative_to(tex_root)) for c in candidates)
    raise IngestError(
        f"Multiple .tex files look like the main file ({listing}). Pass the right one directly: --source <main.tex>."
    )


def flatten_tex(main_file: Path) -> tuple[str, list[str]]:
    """Inline \\input/\\include/\\subfile directives recursively.

    Returns (flattened_text, warnings). Missing files are left in place with a
    warning; \\bibliography and \\input of .bbl files are left for the reader.
    Comments (from an unescaped % to end of line) are kept verbatim -
    fidelity beats tidiness - and directives inside them are not inlined, so a
    chapter the author disabled with `% \\include{ch2}` stays disabled.
    """
    warnings: list[str] = []
    seen: set[Path] = set()

    def flatten(path: Path) -> str:
        resolved = path.resolve()
        if resolved in seen:
            warnings.append(f"cycle: {path} (already inlined); leaving later references as-is")
            return f"% [repoprover] cycle back to {path.name} suppressed\n"
        seen.add(resolved)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            warnings.append(f"cannot read {path}: {e}")
            return ""
        base_dir = path.parent

        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name.endswith(".bbl"):
                return match.group(0)  # bibliography contents stay external
            candidate = base_dir / name
            if not candidate.suffix:
                candidate = candidate.with_suffix(".tex")
            if not candidate.exists():
                warnings.append(f"input not found: {name} (referenced by {path.name}); left as-is")
                return match.group(0)
            return flatten(candidate)

        out: list[str] = []
        for line in text.splitlines(keepends=True):
            comment = _COMMENT_START_RE.search(line)
            split = comment.end() - 1 if comment else len(line)
            out.append(_INPUT_RE.sub(replace, line[:split]) + line[split:])
        return "".join(out)

    return flatten(main_file), warnings


def parse_newtheorem_environments(preamble: str) -> list[str]:
    """Environment names declared via \\newtheorem, in declaration order."""
    return list(dict.fromkeys(m.group(1).strip() for m in _NEWTHEOREM_RE.finditer(preamble)))


def _result_environments(preamble: str) -> list[str]:
    """Environments that count as results: declared ones plus the defaults."""
    declared = parse_newtheorem_environments(preamble)
    return list(dict.fromkeys(declared + DEFAULT_RESULT_ENVIRONMENTS))


def extract_targets(body: str, environments: list[str]) -> tuple[list[str], int]:
    """Labels of results in a body of tex.

    A result is an occurrence of one of the environments (or its starred
    variant); its target id is the first \\label inside it, with ":" mapped to
    "." (the LaTeX label `thm:main` becomes the target `thm.main`, matching
    the Lean declaration style of the book projects). Returns
    (labels, count_of_results_without_a_label).
    """
    labels: list[str] = []
    unlabeled = 0
    hits: list[tuple[int, str | None]] = []  # (position in body, label or None)
    for env in environments:
        pattern = re.compile(r"\\begin\{(" + re.escape(env) + r"\*?)\}(.*?)\\end\{\1\}", re.DOTALL)
        for match in pattern.finditer(body):
            label_match = re.search(r"\\label\{([^}]+)\}", match.group(2))
            label = label_match.group(1).strip().replace(":", ".") if label_match else None
            hits.append((match.start(), label))
    for _, label in sorted(hits):  # document order, not grouped by environment
        if label is None:
            unlabeled += 1
        else:
            labels.append(label)
    return labels, unlabeled


def slugify(text: str) -> str:
    """Turn a section title into a filesystem-safe slug."""
    slug = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    return slug[:40].strip("-") or "section"


def parse_title(flattened: str, fallback: str) -> str:
    """Extract \\title{...}, cleaned of macros; falls back to the given name."""
    match = re.search(r"\\title\s*\{", flattened)
    if match:
        try:
            raw, _ = _extract_braced(flattened, match.end() - 1)
        except IngestError:
            raw = ""
        # Strip \thanks{...} / \thanks[...] and common formatting macros, then braces
        raw = re.sub(r"\\thanks\s*(\[[^\]]*\])?(\{[^}]*\})?", "", raw)
        raw = re.sub(r"\\[a-zA-Z@]+\*?(\[[^\]]*\])?", " ", raw)
        raw = raw.replace("{", "").replace("}", "")
        raw = re.sub(r"\s+", " ", raw).strip()
        if raw:
            return raw
    return fallback


def split_sections(flattened: str, environments: list[str]) -> tuple[str, list[PaperSection], int]:
    """Split a flattened paper into preamble and top-level sections.

    Returns (preamble, sections, unlabeled_result_count). The section list
    covers the whole body: text before the first \\section becomes a
    "front-matter" section; sections after \\appendix get appendix- ids.
    """
    begin_match = re.search(r"\\begin\{document\}", flattened)
    if begin_match:
        preamble = flattened[: begin_match.start()]
        rest = flattened[begin_match.end() :]
        end_match = re.search(r"\\end\{document\}", rest)
        body = rest[: end_match.start()] if end_match else rest
    else:
        preamble = ""
        body = flattened

    # Locate top-level section starts (skip comment lines)
    starts: list[tuple[int, str]] = []  # (offset in body, title)
    position = 0
    for line in body.splitlines(keepends=True):
        if not _COMMENT_LINE_RE.match(line):
            if _APPENDIX_RE.match(line):
                pass  # handled per-section via _appendix_active_at
            else:
                match = _SECTION_RE.search(line)
                if match:
                    try:
                        title, _ = _extract_braced(line, match.end() - 1)
                    except IngestError:
                        title = ""
                    starts.append((position, title.strip()))
        position += len(line)

    sections: list[PaperSection] = []
    used_ids: set[str] = set()
    unlabeled_total = 0

    def make_section(title: str, text: str, prefix: str | None) -> PaperSection:
        nonlocal unlabeled_total
        slug = slugify(title or "section")
        base_id = f"{prefix}-{slug}" if prefix else slug
        section_id = base_id
        suffix = 2
        while section_id in used_ids:
            section_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(section_id)
        targets, unlabeled = extract_targets(text, environments)
        unlabeled_total += unlabeled
        return PaperSection(
            title=title or section_id,
            body=text,
            file_name="",
            id=section_id,
            is_appendix=prefix == "appendix",
            targets=targets,
            unlabeled_results=unlabeled,
        )

    # Front matter (before the first section), if it has real content
    first_section_start = starts[0][0] if starts else len(body)
    front = body[:first_section_start]
    if _has_content(front):
        sections.append(make_section("Front matter", front, None))

    # Sections, each ending where the next begins
    for i, (start, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(body)
        prefix = "appendix" if _appendix_active_at(body, start) else None
        sections.append(make_section(title, body[start:end], prefix))

    # Number the files now that the full list is known
    for i, section in enumerate(sections):
        section.file_name = f"{i:02d}-{section.id}.tex"

    return preamble, sections, unlabeled_total


def _appendix_active_at(body: str, offset: int) -> bool:
    """Whether \\appendix has appeared before the given offset in the body."""
    return bool(_APPENDIX_RE.search(body[:offset]))


def _has_content(text: str) -> bool:
    """Whether tex text says anything (comment-only text does not)."""
    return any(line.strip() and not _COMMENT_LINE_RE.match(line) for line in text.splitlines())


# =============================================================================
# Ingestion
# =============================================================================


def ingest_paper(
    project_dir: Path,
    *,
    arxiv: str | None = None,
    source: Path | None = None,
    project_name: str | None = None,
    tex_dir: str = "tex",
    split: str = "section",
    force: bool = False,
    fetch: Callable[[str, Path], Path] | None = None,  # injectable for tests
) -> IngestResult:
    """Ingest a paper into project_dir and write manifest/CONTENTS/issues/tex.

    Exactly one of `arxiv` / `source` must be given. Never runs lake or git.
    """
    if (arxiv is None) == (source is None):
        raise IngestError("Pass exactly one of --arxiv <id> or --source <file|dir|tar.gz>.")
    if split not in ("section", "none"):
        raise IngestError(f"--split must be 'section' or 'none', got {split!r}.")

    project_dir = project_dir.resolve()
    manifest_path = project_dir / "manifest.json"
    tex_root = project_dir / tex_dir

    existing = [p.name for p in (manifest_path, tex_root) if p.exists()]
    if existing and not force:
        raise IngestError(
            f"{project_dir} already contains {', '.join(existing)}. "
            f"Pass --force to overwrite, or choose an empty project directory."
        )

    warnings: list[str] = []

    # 1. Obtain the tex tree in a scratch directory (nothing touches the
    #    project until the source is confirmed parseable)
    with tempfile.TemporaryDirectory(prefix="repoprover-ingest-") as tmp:
        tmp_dir = Path(tmp)
        main_file: Path
        if arxiv is not None:
            do_fetch = fetch or fetch_arxiv_source
            eprint = do_fetch(arxiv, tmp_dir / "download")
            tex_tree = unpack_eprint(eprint, tmp_dir / "tree")
            main_file = find_main_file(tex_tree)
        else:
            assert source is not None
            source = source.resolve()
            if not source.exists():
                raise IngestError(f"--source {source} does not exist.")
            if source.is_dir():
                tex_tree = source
                main_file = find_main_file(tex_tree)
            elif _detect_format(source) in ("gzip", "tar"):
                tex_tree = unpack_eprint(source, tmp_dir / "tree")
                main_file = find_main_file(tex_tree)
            elif source.is_file():
                if source.suffix != ".tex":
                    raise IngestError(
                        f"--source {source.name}: expected a .tex file, directory, or .tar.gz/.gz archive."
                    )
                main_file = source
                tex_tree = source.parent
            else:
                raise IngestError(f"--source {source} does not exist.")

        # 2. Find and flatten the main file
        flattened, flatten_warnings = flatten_tex(main_file)
        warnings.extend(flatten_warnings)

        # 3. Split into preamble + sections and extract targets
        preamble_text = preamble_from(flattened)
        environments = _result_environments(preamble_text)
        preamble, sections, unlabeled = split_sections(flattened, environments)
        title = parse_title(flattened, arxiv or project_dir.name)

        # 4. Collect bibliography files for [cited] decisions (read the bytes
        #    here: the scratch tree disappears when this block exits)
        bib_files = sorted((p.name, p.read_bytes()) for p in tex_tree.rglob("*.b*") if p.suffix in (".bib", ".bbl"))

    # 5. Write the project
    project_dir.mkdir(parents=True, exist_ok=True)
    tex_root.mkdir(parents=True, exist_ok=True)
    files_written: list[str] = []

    (tex_root / "preamble.tex").write_text(preamble.rstrip() + "\n", encoding="utf-8")
    files_written.append(f"{tex_dir}/preamble.tex")

    (tex_root / "all.tex").write_text(flattened, encoding="utf-8")
    files_written.append(f"{tex_dir}/all.tex")

    if split == "section" and sections:
        for section in sections:
            (tex_root / section.file_name).write_text(section.body.rstrip() + "\n", encoding="utf-8")
            files_written.append(f"{tex_dir}/{section.file_name}")
        units = [
            Unit(
                id=section.id,
                title=section.title,
                source_path=f"{tex_dir}/{section.file_name}",
                target_theorems=section.targets,
            )
            for section in sections
        ]
    else:
        labels, unlabeled = extract_targets(_body_of(flattened), environments)
        units = [Unit(id="all", title=title or "Paper", source_path=f"{tex_dir}/all.tex", target_theorems=labels)]

    for bib_name, bib_bytes in bib_files:
        target = tex_root / bib_name
        if not target.exists():
            target.write_bytes(bib_bytes)
            files_written.append(f"{tex_dir}/{bib_name}")

    manifest = Manifest(
        kind=SourceKind.PAPER,
        title=title,
        units=units,
        arxiv_id=arxiv,
        source_url=ARXIV_ABS_URL.format(arxiv_id=arxiv) if arxiv else None,
    )
    manifest.save(manifest_path)
    files_written.append("manifest.json")

    (project_dir / "CONTENTS.md").write_text(render_contents_md(manifest), encoding="utf-8")
    files_written.append("CONTENTS.md")

    issues_dir = project_dir / "issues"
    issues_dir.mkdir(exist_ok=True)
    (issues_dir / "README.md").write_text(
        "# Issues\n\nEach `.yaml` file is one issue.\n"
        "Close by changing `status: open` to `status: closed`.\n"
        "Filenames are random hex IDs to avoid conflicts.\n",
        encoding="utf-8",
    )
    files_written.append("issues/README.md")

    _ensure_gitignore(project_dir)

    return IngestResult(
        manifest=manifest,
        sections=sections if split == "section" else [],
        unlabeled_results=unlabeled,
        warnings=warnings,
        files_written=files_written,
        title=title,
    )


def preamble_from(flattened: str) -> str:
    """The preamble of a flattened document (before \\begin{document})."""
    match = re.search(r"\\begin\{document\}", flattened)
    return flattened[: match.start()] if match else ""


def _body_of(flattened: str) -> str:
    """The body of a flattened document (between \\begin{document} and \\end{document})."""
    begin = re.search(r"\\begin\{document\}", flattened)
    if not begin:
        return flattened
    rest = flattened[begin.end() :]
    end = re.search(r"\\end\{document\}", rest)
    return rest[: end.start()] if end else rest


def _ensure_gitignore(project_dir: Path) -> None:
    """Keep RepoProver run state out of git."""
    gitignore = project_dir / ".gitignore"
    required = [".repoprover/", "runs/"]
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        missing = [entry for entry in required if entry not in content]
        if missing:
            if not content.endswith("\n"):
                content += "\n"
            gitignore.write_text(content + "\n".join(missing) + "\n", encoding="utf-8")
    else:
        gitignore.write_text("\n".join(required) + "\n", encoding="utf-8")


def print_next_steps(result: IngestResult, project_dir: Path, project_name: str) -> None:
    """Print the lake/git setup steps init-paper deliberately does not run."""
    kind_word = "sections" if result.sections else "body"
    print(f"Ingested paper: {result.title}")
    print(f"  Units: {len(result.manifest.units)} {kind_word}")
    total_targets = sum(len(u.target_theorems) for u in result.manifest.units)
    print(f"  Target theorems (labeled results): {total_targets}")
    if result.unlabeled_results:
        print(f"  Unlabeled results: {result.unlabeled_results} (add \\label{{}} in the tex to make them targets)")
    for warning in result.warnings:
        print(f"  Warning: {warning}")
    print()
    print("Next steps (init-paper does not run lake or git):")
    print(f"  1. cd {project_dir}")
    print(f"  2. lake init {project_name} math   # if not already a lake project")
    print("  3. lake update && lake build && lake build REPL")
    print("  4. Make sure the lakefile's lean_lib covers where agents write Lean (see docs/project-setup.md)")
    print("  5. git init -b main && git add -A && git commit -m 'Import paper'")
    print("  6. repoprover run .")
    print()
    print("See docs/sources.md for the manifest format and target conventions.")
