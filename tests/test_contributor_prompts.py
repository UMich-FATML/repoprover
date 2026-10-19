# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""Tests for kind-aware agent prompts.

The prompt constants are templates; a placeholder typo would raise KeyError
only at run time, mid-flight on an agent. These tests format every template
for both source kinds and pin the paper/book wording, so a broken template
fails in CI instead of during a formalization run.
"""

from __future__ import annotations

import string

import pytest

from repoprover.agents import ContributorTask
from repoprover.agents.contributor import (
    CONTRIBUTOR_BASE_PROMPT,
    MAINTAIN_MODE_PROMPT,
    PAPER_SKETCH_GUIDANCE,
    PROGRESS_MODE_PROMPT,
    PROVE_MODE_PROMPT,
    SCAN_MODE_PROMPT,
    SKETCH_MODE_PROMPT,
    TRIAGE_MODE_PROMPT,
    ContributorAgent,
)
from repoprover.types import ReviewContext

# Placeholders each template is allowed to use. scripts/lint.py enforces the
# same sets statically; keep them in sync.
ALLOWED_PLACEHOLDERS = {
    "CONTRIBUTOR_BASE_PROMPT": {"project_noun"},
    "SKETCH_MODE_PROMPT": {"chapter_id", "source_tex_path", "lean_path", "unit_noun", "unit_noun_cap"},
    "PROVE_MODE_PROMPT": {
        "chapter_id",
        "theorem_name",
        "lean_path",
        "source_tex_path",
        "unit_noun",
        "unit_noun_cap",
    },
    "MAINTAIN_MODE_PROMPT": {"issue_id"},
    "PROGRESS_MODE_PROMPT": {"unit_noun"},
    "SCAN_MODE_PROMPT": set(),
    "TRIAGE_MODE_PROMPT": set(),
    "PAPER_SKETCH_GUIDANCE": set(),
}

PROMPT_CONSTANTS = {
    "CONTRIBUTOR_BASE_PROMPT": CONTRIBUTOR_BASE_PROMPT,
    "SKETCH_MODE_PROMPT": SKETCH_MODE_PROMPT,
    "PROVE_MODE_PROMPT": PROVE_MODE_PROMPT,
    "MAINTAIN_MODE_PROMPT": MAINTAIN_MODE_PROMPT,
    "PROGRESS_MODE_PROMPT": PROGRESS_MODE_PROMPT,
    "SCAN_MODE_PROMPT": SCAN_MODE_PROMPT,
    "TRIAGE_MODE_PROMPT": TRIAGE_MODE_PROMPT,
    "PAPER_SKETCH_GUIDANCE": PAPER_SKETCH_GUIDANCE,
}


def placeholders(template: str) -> set[str]:
    """Extract {placeholder} names from a template string."""
    return {field for _, field, _, _ in string.Formatter().parse(template) if field is not None}


def make_agent(task: ContributorTask) -> ContributorAgent:
    """Construct a ContributorAgent without touching the network or the filesystem."""
    return ContributorAgent(config=None, task=task)


class TestPlaceholders:
    @pytest.mark.parametrize("name", sorted(PROMPT_CONSTANTS))
    def test_placeholders_are_known(self, name: str) -> None:
        extra = placeholders(PROMPT_CONSTANTS[name]) - ALLOWED_PLACEHOLDERS[name]
        assert not extra, (
            f"{name} has unexpected placeholders {extra}; update ALLOWED_PLACEHOLDERS here and in scripts/lint.py"
        )


class TestSystemPrompt:
    def test_book_sketch_prompt(self) -> None:
        agent = make_agent(ContributorTask.sketch("ch1", source_tex_path="tex/ch1.tex", source_kind="book"))
        prompt = agent.get_system_prompt()
        assert "book formalization project" in prompt
        assert "for a chapter." in prompt
        assert "Preamble and notation" not in prompt  # paper guidance absent

    def test_paper_sketch_prompt(self) -> None:
        agent = make_agent(
            ContributorTask.sketch("intro", source_tex_path="tex/01-introduction.tex", source_kind="paper")
        )
        prompt = agent.get_system_prompt()
        assert "paper formalization project" in prompt
        assert "for a section." in prompt
        assert "Source-Specific Guidance: Papers" in prompt
        assert "Preamble and notation" in prompt
        assert "Main theorem placement" in prompt

    def test_book_prove_prompt(self) -> None:
        agent = make_agent(ContributorTask.prove("ch1", "thm:main", source_tex_path="tex/ch1.tex", source_kind="book"))
        prompt = agent.get_system_prompt()
        assert "the chapter's Lean file" in prompt

    def test_paper_prove_prompt(self) -> None:
        agent = make_agent(ContributorTask.prove("results", "thm:main", source_kind="paper"))
        prompt = agent.get_system_prompt()
        assert "the section's Lean file" in prompt
        assert "paper formalization project" in prompt

    def test_paper_progress_prompt(self) -> None:
        agent = make_agent(ContributorTask.progress(source_kind="paper"))
        prompt = agent.get_system_prompt()
        assert "Each section in the manifest" in prompt

    def test_book_progress_prompt(self) -> None:
        agent = make_agent(ContributorTask.progress(source_kind="book"))
        prompt = agent.get_system_prompt()
        assert "Each chapter in the manifest" in prompt

    @pytest.mark.parametrize(
        "factory",
        [
            lambda kind: ContributorTask.maintain(issue_id="abcd1234", source_kind=kind),
            lambda kind: ContributorTask.scan(source_kind=kind),
            lambda kind: ContributorTask.triage(source_kind=kind),
            lambda kind: ContributorTask.fix(source_kind=kind),
            lambda kind: ContributorTask.progress(source_kind=kind),
        ],
        ids=["maintain", "scan", "triage", "fix", "progress"],
    )
    @pytest.mark.parametrize("kind", ["book", "paper"])
    def test_all_modes_format_for_both_kinds(self, factory, kind: str) -> None:
        agent = make_agent(factory(kind))
        prompt = agent.get_system_prompt()
        assert f"{kind} formalization project" in prompt
        # No unformatted placeholder should leak into the final prompt
        assert "{project_noun}" not in prompt
        assert "{unit_noun}" not in prompt

    @pytest.mark.parametrize(
        "task",
        [
            ContributorTask.maintain(issue_id="abcd1234"),
            ContributorTask.scan(),
            ContributorTask.triage(),
            ContributorTask.progress(),
        ],
        ids=["maintain", "scan", "triage", "progress"],
    )
    def test_non_sketch_modes_leave_unit_files_to_sketchers(self, task) -> None:
        # Support agents scaffolding unsketched chapter files made every in-flight sketch conflict.
        prompt = make_agent(task).get_system_prompt()
        assert "Unit files belong to their sketcher" in prompt
        assert "do not create, scaffold, stub or import a missing unit file" in prompt

    def test_unknown_source_kind_falls_back_to_book(self) -> None:
        agent = make_agent(ContributorTask.scan(source_kind="pamphlet"))
        prompt = agent.get_system_prompt()
        assert "book formalization project" in prompt

    def test_default_source_kind_is_book(self) -> None:
        # Old wire messages have no source_kind field; the default must keep them valid
        assert ContributorTask.scan().source_kind == "book"
        agent = make_agent(ContributorTask.triage())
        assert "book formalization project" in agent.get_system_prompt()


class TestUserPrompt:
    def test_sketch_user_prompt_uses_unit_noun(self) -> None:
        agent = make_agent(
            ContributorTask.sketch("intro", source_tex_path="tex/01-introduction.tex", source_kind="paper")
        )
        prompt = agent.build_user_prompt(is_initial=True)
        assert "## Section" in prompt
        assert "## Chapter" not in prompt

    def test_sketch_user_prompt_book(self) -> None:
        agent = make_agent(ContributorTask.sketch("ch1", source_tex_path="tex/ch1.tex", source_kind="book"))
        assert "## Chapter" in agent.build_user_prompt(is_initial=True)

    def test_prove_user_prompt_uses_unit_noun(self) -> None:
        agent = make_agent(ContributorTask.prove("results", "thm:main", source_kind="paper"))
        prompt = agent.build_user_prompt()
        assert "- Section: `results`" in prompt


class TestReviewContext:
    def test_source_kind_defaults_to_book(self) -> None:
        ctx = ReviewContext(
            pr_id="p",
            branch_name="b",
            agent_type="sketch",  # type: ignore[arg-type]
            agent_id="a",
            chapter_id="c",
            title="t",
            files_changed=[],
        )
        assert ctx.source_kind == "book"
