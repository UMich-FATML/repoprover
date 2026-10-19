# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""Tests for scripts/lint.py — the repo's custom invariant checks.

The lint guards agent-facing invariants (docs links, plan status lines,
prompt placeholders); a silently-broken lint would let those rot, so the
lint's own failure modes are pinned here.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LINT_PATH = REPO_ROOT / "scripts" / "lint.py"


@pytest.fixture(scope="module")
def lint():
    """Load scripts/lint.py as a module (it is not in a package)."""
    spec = importlib.util.spec_from_file_location("repo_lint", LINT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLintPassesOnRepo:
    def test_exit_zero_on_current_tree(self) -> None:
        """The whole point: the checked-in repo satisfies its own lints."""
        result = subprocess.run([sys.executable, str(LINT_PATH)], capture_output=True, text=True, cwd=REPO_ROOT)
        assert result.returncode == 0, f"lint output:\n{result.stdout}\n{result.stderr}"

    def test_reports_ok_per_check(self, lint) -> None:
        for label, check in lint.CHECKS:
            assert check() == [], f"{label} should pass on the repo"


class TestAgentsMdSize:
    def test_oversized_agents_md_is_flagged_with_fix_hint(self, lint, monkeypatch) -> None:
        monkeypatch.setattr(lint, "AGENTS_MD_MAX_LINES", 5)
        errors = lint.check_agents_md_size()
        assert errors and "docs/" in errors[0] and "lines" in errors[0]


class TestDocLinks:
    def test_missing_relative_link_is_flagged(self, lint, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "AGENTS.md").write_text("map\n")
        (tmp_path / "docs" / "page.md").write_text("see [guide](missing.md)\n")
        monkeypatch.setattr(lint, "REPO_ROOT", tmp_path)
        errors = lint.check_doc_links()
        assert errors and "does not resolve" in errors[0]

    def test_external_and_anchor_links_are_ignored(self, lint, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "AGENTS.md").write_text("[site](https://example.com/a) [anchor](#top) [local](README2.md)\n")
        (tmp_path / "README2.md").write_text("x\n")
        monkeypatch.setattr(lint, "REPO_ROOT", tmp_path)
        assert lint.check_doc_links() == []


class TestPlansStatus:
    def test_plan_without_status_is_flagged(self, lint, tmp_path: Path, monkeypatch) -> None:
        plans = tmp_path / "docs" / "plans"
        plans.mkdir(parents=True)
        (plans / "a-plan.md").write_text("# A plan\n\nNo status line here.\n")
        monkeypatch.setattr(lint, "REPO_ROOT", tmp_path)
        errors = lint.check_plans_status()
        assert errors and "Status:" in errors[0]


class TestManifests:
    def test_broken_manifest_is_flagged(self, lint, tmp_path: Path, monkeypatch) -> None:
        examples = tmp_path / "examples" / "proj"
        examples.mkdir(parents=True)
        (examples / "manifest.json").write_text('{"kind": "book", "title": "T", "chapters": "nope"}')
        monkeypatch.setattr(lint, "REPO_ROOT", tmp_path)
        errors = lint.check_manifests_load()
        assert errors and "does not load" in errors[0]


class TestPromptPlaceholders:
    def test_drifted_documentation_is_flagged(self, lint, monkeypatch) -> None:
        documented = dict(lint.PROMPT_PLACEHOLDERS)
        try:
            lint.PROMPT_PLACEHOLDERS["SKETCH_MODE_PROMPT"] = {"bogus_placeholder"}
            errors = lint.check_prompt_placeholders()
            assert errors and "SKETCH_MODE_PROMPT" in errors[0]
            assert "test_contributor_prompts" in errors[0]  # points at both lists to fix
        finally:
            lint.PROMPT_PLACEHOLDERS.clear()
            lint.PROMPT_PLACEHOLDERS.update(documented)


class TestFileSizes:
    def test_oversized_unlisted_file_is_flagged(self, lint, monkeypatch) -> None:
        monkeypatch.setattr(lint, "LARGE_FILE_MAX_LINES", 10)
        errors = lint.check_file_sizes()
        # Files listed in docs/tech-debt.md must NOT be flagged...
        assert not any("coordinator.py" in e or "viewer.html" in e for e in errors)
        # ...but unlisted oversized files must be, with the remedy.
        assert any("docs/tech-debt.md" in e for e in errors)
