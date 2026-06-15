"""Tests for workspace utilities."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from wflow.common.workspace import detect_wflow_dir, setup_work_dir


# ── detect_wflow_dir ──────────────────────────────────────────────────────────


def test_detect_wflow_dir_exists(tmp_path: Path):
    """Returns the Path when .wflow exists."""
    (tmp_path / ".wflow").mkdir()
    result = detect_wflow_dir(tmp_path)
    assert result == (tmp_path / ".wflow").resolve()


def test_detect_wflow_dir_not_exists(tmp_path: Path):
    """Returns None when .wflow does not exist."""
    result = detect_wflow_dir(tmp_path)
    assert result is None


def test_detect_wflow_dir_ignores_other_dirs(tmp_path: Path):
    """Only .wflow is detected; .claude and .opencode are ignored."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".opencode").mkdir()
    result = detect_wflow_dir(tmp_path)
    assert result is None


def test_detect_wflow_dir_not_a_directory(tmp_path: Path):
    """Returns None when .wflow is a file, not a directory."""
    (tmp_path / ".wflow").touch()
    result = detect_wflow_dir(tmp_path)
    assert result is None


# ── setup_work_dir fixtures ───────────────────────────────────────────────────


@pytest.fixture
def mock_symlink():
    """Mock os.symlink so tests work without admin/Developer Mode."""
    with patch("wflow.common.workspace.os.symlink") as mock:
        yield mock


@pytest.fixture
def wflow_with_subs(tmp_path: Path) -> Path:
    """Create a .wflow dir with skills/ and agents/ subdirs."""
    wf = tmp_path / ".wflow"
    wf.mkdir()
    (wf / "skills").mkdir()
    (wf / "agents").mkdir()
    return wf


@pytest.fixture
def wflow_empty(tmp_path: Path) -> Path:
    """Create a bare .wflow dir without subdirs."""
    wf = tmp_path / ".wflow"
    wf.mkdir()
    return wf


# ── setup_work_dir: always creates .claude/ and .opencode/ ───────────────────


def test_setup_always_creates_tool_dirs(tmp_path: Path):
    """setup_work_dir ALWAYS creates .claude/ and .opencode/ under work_dir,
    even without a .wflow directory."""
    work_dir = tmp_path / "work"
    setup_work_dir(work_dir)  # no wflow_dir

    assert (work_dir / ".claude").is_dir()
    assert (work_dir / ".opencode").is_dir()


def test_setup_creates_tool_dirs_with_wflow(
    wflow_with_subs: Path, tmp_path: Path, mock_symlink,
):
    """With wflow_dir, .claude/ and .opencode/ are still created."""
    work_dir = tmp_path / "work"
    setup_work_dir(work_dir, wflow_dir=wflow_with_subs)

    assert (work_dir / ".claude").is_dir()
    assert (work_dir / ".opencode").is_dir()


def test_setup_creates_work_dir_if_missing(
    wflow_with_subs: Path, tmp_path: Path, mock_symlink,
):
    """work_dir is created if it doesn't already exist."""
    work_dir = tmp_path / "work"
    # work_dir does NOT exist yet
    setup_work_dir(work_dir, wflow_dir=wflow_with_subs)
    assert work_dir.is_dir()
    assert (work_dir / ".claude").is_dir()


# ── setup_work_dir: symlinks when .wflow present ─────────────────────────────


def test_setup_symlinks_skills_and_agents(
    wflow_with_subs: Path, tmp_path: Path, mock_symlink,
):
    """skills/ and agents/ are symlinked into .claude/ and .opencode/."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    setup_work_dir(work_dir, wflow_dir=wflow_with_subs)

    expected_calls = [
        # .wflow → work/.wflow
        ((str(wflow_with_subs), str(work_dir / ".wflow")),),
        # skills → .claude/skills
        ((str(wflow_with_subs / "skills"), str(work_dir / ".claude" / "skills")),),
        # agents → .claude/agents
        ((str(wflow_with_subs / "agents"), str(work_dir / ".claude" / "agents")),),
        # skills → .opencode/skills
        ((str(wflow_with_subs / "skills"), str(work_dir / ".opencode" / "skills")),),
        # agents → .opencode/agents
        ((str(wflow_with_subs / "agents"), str(work_dir / ".opencode" / "agents")),),
    ]
    assert mock_symlink.call_count == 5
    for i, expected in enumerate(expected_calls):
        call_args = mock_symlink.call_args_list[i][0]
        assert call_args[0] == expected[0][0]
        assert call_args[1] == expected[0][1]
        assert mock_symlink.call_args_list[i][1].get("target_is_directory") is True


def test_setup_symlink_uses_target_is_directory(
    wflow_with_subs: Path, tmp_path: Path, mock_symlink,
):
    """Every symlink call passes target_is_directory=True."""
    work_dir = tmp_path / "work"
    setup_work_dir(work_dir, wflow_dir=wflow_with_subs)
    for call in mock_symlink.call_args_list:
        assert call[1].get("target_is_directory") is True


# ── setup_work_dir: skip / resume ────────────────────────────────────────────


def test_setup_skips_existing_destinations(
    wflow_with_subs: Path, tmp_path: Path, mock_symlink,
):
    """When destination already exists, symlink is skipped (resume scenario)."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    # Pre-create .claude/, .opencode/, .wflow destinations
    (work_dir / ".claude").mkdir(exist_ok=True)
    (work_dir / ".opencode").mkdir(exist_ok=True)
    (work_dir / ".wflow").mkdir()

    setup_work_dir(work_dir, wflow_dir=wflow_with_subs)

    # .wflow symlink was skipped because dst exists; only 4 calls for skills/agents
    assert mock_symlink.call_count == 4


# ── setup_work_dir: missing subdirs ──────────────────────────────────────────


def test_setup_skips_missing_subdirs(
    wflow_empty: Path, tmp_path: Path, mock_symlink,
):
    """When skills/ or agents/ don't exist, they are skipped without error."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    setup_work_dir(work_dir, wflow_dir=wflow_empty)

    # Only the .wflow symlink was made — skills/agents skipped
    assert mock_symlink.call_count == 1
    call_args = mock_symlink.call_args_list[0][0]
    assert call_args[1] == str(work_dir / ".wflow")


def test_setup_logs_warning_when_subs_missing(
    wflow_empty: Path, tmp_path: Path, mock_symlink, caplog,
):
    """When skills/agents are missing, warnings are logged."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    logger = logging.getLogger("test_workspace")
    logger.setLevel(logging.WARNING)

    with caplog.at_level(logging.WARNING, logger="test_workspace"):
        setup_work_dir(work_dir, wflow_dir=wflow_empty, logger=logger)

    assert len(caplog.records) == 4
    warning_texts = [r.message for r in caplog.records]
    assert any("skills" in t for t in warning_texts)
    assert any("agents" in t for t in warning_texts)
    assert any(".claude" in t for t in warning_texts)
    assert any(".opencode" in t for t in warning_texts)
    for record in caplog.records:
        assert record.levelno == logging.WARNING


# ── setup_work_dir: graceful degradation ─────────────────────────────────────


def test_setup_handles_missing_wflow_dir_gracefully(tmp_path: Path):
    """When wflow_dir path doesn't exist on disk, .claude/ and .opencode/
    are still created, and a warning is logged."""
    work_dir = tmp_path / "work"
    missing_wflow = tmp_path / ".wflow"  # never created

    # Should not raise
    setup_work_dir(work_dir, wflow_dir=missing_wflow)

    assert (work_dir / ".claude").is_dir()
    assert (work_dir / ".opencode").is_dir()


def test_setup_handles_wflow_is_file_gracefully(tmp_path: Path):
    """When wflow_dir is a file (not a dir), .claude/ and .opencode/
    are still created, no error raised."""
    work_dir = tmp_path / "work"
    not_a_dir = tmp_path / ".wflow"
    not_a_dir.touch()

    # Should not raise
    setup_work_dir(work_dir, wflow_dir=not_a_dir)

    assert (work_dir / ".claude").is_dir()
    assert (work_dir / ".opencode").is_dir()


def test_setup_handles_none_wflow_dir(tmp_path: Path):
    """wflow_dir=None is fine — just creates .claude/ and .opencode/."""
    work_dir = tmp_path / "work"
    setup_work_dir(work_dir)  # no wflow_dir

    assert (work_dir / ".claude").is_dir()
    assert (work_dir / ".opencode").is_dir()


# ── setup_work_dir: symlink / junction fallback ─────────────────────────────


def test_symlink_failure_propagates_on_non_windows(
    wflow_with_subs: Path, tmp_path: Path,
):
    """On non-Windows, OSError from os.symlink propagates directly."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    with patch("wflow.common.workspace.sys.platform", "linux"):
        with patch("wflow.common.workspace.os.symlink", side_effect=OSError("permission denied")):
            with pytest.raises(OSError, match="permission denied"):
                setup_work_dir(work_dir, wflow_dir=wflow_with_subs)

    # .claude/ and .opencode/ were still created before the symlink attempt
    assert (work_dir / ".claude").is_dir()
    assert (work_dir / ".opencode").is_dir()


def test_windows_fallback_to_mklink_junction(
    wflow_with_subs: Path, tmp_path: Path,
):
    """On Windows, when os.symlink fails, fall back to mklink /J."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    with patch("wflow.common.workspace.sys.platform", "win32"):
        with patch("wflow.common.workspace.os.symlink", side_effect=OSError("privilege")):
            with patch("wflow.common.workspace.subprocess.run") as mock_run:
                setup_work_dir(work_dir, wflow_dir=wflow_with_subs)

    # mklink /J should have been called 5 times (wflow + 2 skills + 2 agents)
    assert mock_run.call_count == 5
    for call in mock_run.call_args_list:
        args = call[0][0]
        assert args[0] == "cmd"
        assert args[1] == "/c"
        assert args[2] == "mklink"
        assert args[3] == "/J"


def test_windows_mklink_failure_propagates(
    wflow_with_subs: Path, tmp_path: Path,
):
    """On Windows, when both os.symlink and mklink fail, OSError propagates."""
    import subprocess as sp

    work_dir = tmp_path / "work"
    work_dir.mkdir()

    with patch("wflow.common.workspace.sys.platform", "win32"):
        with patch("wflow.common.workspace.os.symlink", side_effect=OSError("privilege")):
            with patch("wflow.common.workspace.subprocess.run") as mock_run:
                mock_run.side_effect = sp.CalledProcessError(
                    1, "cmd", stderr="access denied",
                )
                with pytest.raises(OSError, match="Failed to create directory link"):
                    setup_work_dir(work_dir, wflow_dir=wflow_with_subs)

    # .claude/ and .opencode/ were still created
    assert (work_dir / ".claude").is_dir()
    assert (work_dir / ".opencode").is_dir()
