"""Coordinator resilience to transient failures seen in the 11-book runs.

A briefly full filesystem, a gateway restart or a racing state dict used to
crash the coordinator; it then drained for hours and exited.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repoprover.coordinator import (
    LOOP_MAX_CONSECUTIVE_ERRORS,
    Coordinator,
    RunState,
    loop_retry_delay,
)


def test_retry_delay_backs_off_and_caps():
    assert [loop_retry_delay(n) for n in (1, 2, 3, 6, 50)] == [10.0, 20.0, 40.0, 300.0, 300.0]
    assert LOOP_MAX_CONSECUTIVE_ERRORS >= 10


def test_save_retries_a_racing_snapshot(tmp_path: Path, monkeypatch) -> None:
    state = RunState(project_id="book")
    real = RunState._serialize
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dictionary changed size during iteration")
        return real(self)

    monkeypatch.setattr(RunState, "_serialize", flaky)
    state.save(tmp_path / "state.json")
    assert calls["n"] == 2
    assert RunState.load(tmp_path / "state.json").project_id == "book"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def test_recover_base_checkout_aborts_merge_and_returns_to_main(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.lean").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "feature")
    (repo / "a.lean").write_text("feature\n")
    _git(repo, "commit", "-qam", "feature")
    _git(repo, "checkout", "-q", "main")
    (repo / "a.lean").write_text("main\n")
    _git(repo, "commit", "-qam", "main")
    _git(repo, "checkout", "-q", "feature")
    assert _git(repo, "merge", "main").returncode != 0  # conflict: MERGE_HEAD left behind, on a branch

    coordinator = object.__new__(Coordinator)
    coordinator.base_project = repo
    coordinator._recover_base_checkout()

    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert _git(repo, "branch", "--show-current").stdout.strip() == "main"
