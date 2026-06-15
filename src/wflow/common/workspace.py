"""Workspace utilities — project directory detection and symlink setup."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path


def detect_wflow_dir(project_root: str | Path) -> Path | None:
    """Find the project ``.wflow`` config directory.

    Returns the ``Path`` if ``.wflow`` exists under *project_root*,
    or ``None`` otherwise.
    """
    root = Path(project_root).resolve()
    p = root / ".wflow"
    return p if p.is_dir() else None


def setup_work_dir(
    work_dir: Path,
    wflow_dir: Path | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Set up the run's work directory.

    Always creates ``.claude/`` and ``.opencode/`` subdirectories under
    *work_dir*.  If *wflow_dir* is provided, symlinks ``skills/`` and
    ``agents/`` from ``.wflow`` into each tool directory, and symlinks
    the entire ``.wflow/`` directory itself.

    Uses ``os.symlink`` (or Windows ``mklink /J`` junction fallback).
    No copytree fallback.
    If a destination already exists (resume scenario), it is skipped.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    for tool_dir_name in (".claude", ".opencode"):
        (work_dir / tool_dir_name).mkdir(exist_ok=True)

    if wflow_dir is None:
        if logger:
            logger.debug("Workspace: no .wflow dir — created .claude/ and .opencode/ only")
        return

    if not wflow_dir.is_dir():
        if logger:
            logger.warning(
                "Workspace: .wflow not found at %s — run will proceed without symlinks",
                wflow_dir,
            )
        return

    _symlink_dir(
        src=wflow_dir,
        dst=work_dir / ".wflow",
        logger=logger,
    )

    for tool_dir_name in (".claude", ".opencode"):
        tool_dir = work_dir / tool_dir_name
        for sub in ("skills", "agents"):
            src = wflow_dir / sub
            if not src.is_dir():
                if logger:
                    logger.warning(
                        "Workspace: %s/%s not found — "
                        "run will proceed without %s %s",
                        wflow_dir.name, sub, tool_dir_name, sub,
                    )
                continue
            _symlink_dir(src=src, dst=tool_dir / sub, logger=logger)


def _symlink_dir(
    src: Path,
    dst: Path,
    logger: logging.Logger | None = None,
) -> None:
    """Symlink *src* directory to *dst*, skipping if *dst* already exists.

    On Windows, falls back to ``mklink /J`` (directory junction) when
    ``os.symlink`` fails due to missing privileges (no Developer Mode).
    """
    if dst.exists():
        if logger:
            logger.debug("Workspace: %s already exists, skipping", dst.name)
        return

    try:
        os.symlink(str(src), str(dst), target_is_directory=True)
        if logger:
            logger.info("Workspace: symlinked %s → %s", src.name, dst)
    except OSError:
        if sys.platform != "win32":
            raise
        # Windows without Developer Mode — try mklink /J junction
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
                check=True,
                capture_output=True,
                text=True,
            )
            if logger:
                logger.info("Workspace: junction %s → %s", src.name, dst)
        except subprocess.CalledProcessError as e:
            raise OSError(
                f"Failed to create directory link {dst} → {src}: {e.stderr.strip()}"
            ) from e
