"""Tests for import-only merge-conflict resolution (repoprover.merge_resolve)."""

import subprocess

from repoprover.merge_resolve import auto_resolve_merge, resolve_import_conflicts


def test_unions_import_hunk():
    text = "import Book.Basic\n<<<<<<< HEAD\nimport Book.ChapterA\n=======\nimport Book.ChapterB\n>>>>>>> main\n"
    assert resolve_import_conflicts(text) == "import Book.Basic\nimport Book.ChapterA\nimport Book.ChapterB\n"


def test_diff3_base_dropped_and_duplicates_removed():
    text = (
        "<<<<<<< ours\n"
        "import Book.A\n"
        "import Book.Shared\n"
        "||||||| base\n"
        "import Book.Old\n"
        "=======\n"
        "import Book.Shared\n"
        "import Book.B\n"
        ">>>>>>> theirs\n"
    )
    assert resolve_import_conflicts(text) == "import Book.A\nimport Book.Shared\nimport Book.B\n"


def test_import_side_against_docstring_side():
    # Seen in petrunin-diffgeo: a sketch added an import while main added a module docstring.
    text = "import Book.Basic\n<<<<<<< HEAD\nimport Book.Appendix\n=======\n\n/-! # Book root -/\n>>>>>>> main\n"
    assert resolve_import_conflicts(text) == "import Book.Basic\nimport Book.Appendix\n\n/-! # Book root -/\n"


def test_refuses_when_both_sides_have_code():
    text = "<<<<<<< HEAD\ntheorem a : True := trivial\n=======\ndef b := 1\n>>>>>>> main\n"
    assert resolve_import_conflicts(text) is None


def test_refuses_hunk_below_the_import_header():
    text = "import Book.Basic\n/-! doc -/\n<<<<<<< HEAD\nimport Book.A\n=======\ndef b := 1\n>>>>>>> main\n"
    assert resolve_import_conflicts(text) is None


def test_no_markers_returns_none():
    assert resolve_import_conflicts("import Book.A\n") is None


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def _repo_with_conflict(tmp_path, branch_body: str, main_body: str = "import Book.Basic\nimport Book.ChapterB\n"):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "Book.lean").write_text("import Book.Basic\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "sketch")
    (repo / "Book.lean").write_text(branch_body)
    _git(repo, "commit", "-qam", "sketch")
    _git(repo, "checkout", "-q", "main")
    (repo / "Book.lean").write_text(main_body)
    _git(repo, "commit", "-qam", "other sketch")
    assert _git(repo, "merge", "--no-ff", "sketch", "-m", "Merge sketch").returncode != 0
    return repo


def test_auto_resolve_commits_merge(tmp_path):
    repo = _repo_with_conflict(tmp_path, "import Book.Basic\nimport Book.ChapterA\n")
    assert auto_resolve_merge(repo, ["Book.lean"]) == []
    assert (repo / "Book.lean").read_text() == "import Book.Basic\nimport Book.ChapterB\nimport Book.ChapterA\n"
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "Merge sketch"
    assert _git(repo, "status", "--porcelain").stdout == ""


def test_auto_resolve_leaves_real_conflicts(tmp_path):
    repo = _repo_with_conflict(tmp_path, "import Book.Basic\ndef x := 1\n", "import Book.Basic\ndef y := 2\n")
    assert auto_resolve_merge(repo, ["Book.lean"]) == ["Book.lean"]
