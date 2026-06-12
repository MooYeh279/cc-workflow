"""Session lifecycle manager for Claude CLI sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.models.db import Session, NodeExecution


class SessionManager:
    """Manages Claude CLI session creation, lookup, and cleanup.

    One session per (run_id, node_id) pair.

    Session validity for resume: a session is valid (has conversation data)
    iff at least one NodeExecution record with the same run_id + node_id
    has status='completed'. This avoids filesystem dependency on Claude's
    internal session storage path.
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_or_create(self, run_id: str, node_id: str) -> str:
        """Get existing session ID or create a new one for (run_id, node_id)."""
        stmt = select(Session).where(
            Session.run_id == run_id,
            Session.node_id == node_id,
        )
        result = await self._db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return existing.id

        session_id = str(uuid.uuid4())
        session = Session(
            id=session_id,
            run_id=run_id,
            node_id=node_id,
            status="active",
        )
        self._db.add(session)
        await self._db.commit()
        return session_id

    async def is_session_valid(self, run_id: str, node_id: str) -> bool:
        """Pre-flight: a session is resumable iff a prior node_execution succeeded.

        Checks the DB: if any NodeExecution for this (run_id, node_id) has
        status='completed', the session has real conversation data.
        """
        stmt = select(NodeExecution).where(
            NodeExecution.run_id == run_id,
            NodeExecution.node_id == node_id,
            NodeExecution.status == "completed",
        ).limit(1)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_session_id(self, run_id: str, node_id: str) -> str | None:
        """Get the session ID for a (run_id, node_id) pair, or None."""
        stmt = select(Session).where(
            Session.run_id == run_id,
            Session.node_id == node_id,
        )
        result = await self._db.execute(stmt)
        existing = result.scalar_one_or_none()
        return existing.id if existing else None

    async def mark_completed(self, session_id: str) -> None:
        """Mark a session as completed (no longer active)."""
        stmt = select(Session).where(Session.id == session_id)
        result = await self._db.execute(stmt)
        session = result.scalar_one_or_none()
        if session:
            session.status = "completed"
            await self._db.commit()

    # ── Provider session ID — opencode / claude assigned ID ───────────────

    async def set_provider_session_id(
        self, run_id: str, node_id: str, provider_sid: str,
    ) -> None:
        """Store the actual provider-assigned session ID (e.g. ``ses_xxx``).

        Uses the ``session_path`` column (otherwise unused) to persist the
        provider session ID so it can be retrieved on resume / re-run.
        """
        stmt = select(Session).where(
            Session.run_id == run_id, Session.node_id == node_id,
        )
        result = await self._db.execute(stmt)
        session = result.scalar_one_or_none()
        if session:
            session.session_path = provider_sid
            await self._db.commit()

    async def get_provider_session_id(
        self, run_id: str, node_id: str,
    ) -> str | None:
        """Return the stored provider session ID, or ``None``."""
        stmt = select(Session).where(
            Session.run_id == run_id, Session.node_id == node_id,
        )
        result = await self._db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session or not session.session_path:
            return None
        return session.session_path

    async def cleanup_expired(self, retention_days: int = 30) -> int:
        """Delete sessions older than retention_days. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        stmt = delete(Session).where(
            Session.status == "completed",
            Session.created_at < cutoff,
        )
        result = await self._db.execute(stmt)
        await self._db.commit()
        return result.rowcount
