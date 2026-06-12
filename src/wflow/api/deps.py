"""FastAPI dependency injection and shared helpers."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncSession:
    """Yield an async database session."""
    session_local = request.app.state.SessionLocal
    async with session_local() as session:
        yield session


def get_adapters(request: Request) -> dict[str, Any]:
    """Extract node handlers and config from app state.

    Returns a dict suitable for ``**kwargs`` expansion into
    ``start_and_launch()`` / ``launch_workflow()``.
    """
    return {
        "handlers": request.app.state.handlers,
        "session_factory": request.app.state.SessionLocal,
        "project_dirs": getattr(request.app.state, "project_dirs", []),
    }
