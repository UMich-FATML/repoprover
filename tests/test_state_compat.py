# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""Tests for RunState format compatibility across renames.

State files written by older RepoProver versions must keep loading; the
coordinator resumes from them (see docs/design.md: state-format changes
must stay loadable from older state.json).
"""

from __future__ import annotations

import json
from pathlib import Path

from repoprover.coordinator import RunState


class TestRunStateCompat:
    def test_loads_legacy_book_id_state(self, tmp_path: Path) -> None:
        """State files from before the book→project rename store "book_id"."""
        legacy = {
            "book_id": "algcomb",
            "chapters": {"ch1": {"title": "Intro", "source_path": "tex/ch1.tex", "sketch_merged": True}},
            "prs": {},
            "completed_theorems": {"ch1": ["thm:main"]},
            "next_issue_id": 3,
        }
        path = tmp_path / "state.json"
        path.write_text(json.dumps(legacy))

        state = RunState.load(path)
        assert state.project_id == "algcomb"
        assert state.source_kind == "book"  # defaulted, not stored in legacy files
        assert state.chapters["ch1"]["sketch_merged"] is True
        assert state.completed_theorems == {"ch1": ["thm:main"]}

    def test_save_writes_new_keys_only(self, tmp_path: Path) -> None:
        """Saving writes project_id/source_kind and drops book_id."""
        path = tmp_path / "state.json"
        state = RunState(project_id="mypaper", source_kind="paper")
        state.save(path)

        data = json.loads(path.read_text())
        assert data["project_id"] == "mypaper"
        assert data["source_kind"] == "paper"
        assert "book_id" not in data

        # And it loads back
        reloaded = RunState.load(path)
        assert reloaded.project_id == "mypaper"
        assert reloaded.source_kind == "paper"

    def test_load_rejects_empty_state_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text("")
        try:
            RunState.load(path)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "empty" in str(e)


class TestRunStateAtomicSave:
    def test_failed_save_keeps_previous_file(self, tmp_path: Path, monkeypatch) -> None:
        """A full disk mid-save used to leave a 0-byte state.json (seen in the book runs)."""
        path = tmp_path / "state.json"
        RunState(project_id="book").save(path)
        before = path.read_text()

        import repoprover.coordinator as coord

        def boom(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(coord.os, "fsync", boom)
        state = RunState(project_id="book")
        state.next_issue_id = 99
        try:
            state.save(path)
        except OSError:
            pass
        assert path.read_text() == before
        assert not (tmp_path / "state.json.tmp").exists()
