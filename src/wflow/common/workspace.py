"""Workspace utilities — project directory detection and symlink setup."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path


def detect_project_dirs(project_root: str | Path) -> list[Path]:
    """Find project config directories (.claude, .opencode, .wflow)."""
    root = Path(project_root).resolve()
    found: list[Path] = []
    for name in (".claude", ".opencode", ".wflow"):
        p = root / name
        if p.is_dir():
            found.append(p)
    return found


def link_project_dirs(
    work_dir: str | Path,
    project_dirs: list[Path],
    logger: logging.Logger | None = None,
) -> None:
    """Symlink (or copy) project dirs into a workspace directory.

    Tries ``os.symlink`` first (Unix, Windows with Developer Mode).
    Falls back to ``shutil.copytree`` on permission errors.
    """
    work = Path(work_dir)
    for src in project_dirs:
        dst = work / src.name
        if dst.exists():
            continue  # already linked (resume)
        try:
            os.symlink(str(src), str(dst), target_is_directory=True)
            if logger:
                logger.info(f"Workspace: symlinked {src.name} → {dst}")
        except OSError:
            # Symlink failed (e.g. Windows without dev mode) — fall back to copy
            try:
                shutil.copytree(str(src), str(dst))
                if logger:
                    logger.info(f"Workspace: copied {src.name} → {dst}")
            except Exception as e:
                if logger:
                    logger.warning(f"Workspace: failed to link {src.name}: {e}")
