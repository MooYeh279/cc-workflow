"""Execution service — manages workflow runs."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.common.time_utils import utc_now_iso
from wflow.models.db import WorkflowRun, NodeExecution, RunLog
from wflow.engine.state_machine import RunStateMachine, RunStatus


class ExecutionService:
    def __init__(self, db: AsyncSession, engine: Any = None):
        self._db = db
        self._engine = engine
        self._state_machine = RunStateMachine()

    async def start_run(self, workflow_id: str, inputs: dict[str, Any]) -> WorkflowRun:
        run = WorkflowRun(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            status=RunStatus.PENDING.value,
            context=json.dumps({"inputs": inputs}, ensure_ascii=False),
        )
        self._db.add(run)
        await self._db.commit()
        return run

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_runs(
        self, workflow_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[WorkflowRun]:
        stmt = select(WorkflowRun)
        if workflow_id:
            stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
        if status:
            stmt = stmt.where(WorkflowRun.status == status)
        stmt = stmt.order_by(WorkflowRun.started_at.desc()).limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def pause_run(self, run_id: str) -> WorkflowRun | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        run.status = self._state_machine.transition(
            RunStatus(run.status), RunStatus.PAUSED
        ).value
        await self._db.commit()
        return run

    async def resume_run(self, run_id: str) -> WorkflowRun | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        run.status = self._state_machine.transition(
            RunStatus(run.status), RunStatus.RUNNING
        ).value
        await self._db.commit()
        return run

    async def stop_run(self, run_id: str) -> WorkflowRun | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        run.status = self._state_machine.transition(
            RunStatus(run.status), RunStatus.FAILED
        ).value
        run.finished_at = utc_now_iso()
        await self._db.commit()
        return run

    async def get_logs(
        self, run_id: str, level: str | None = None, limit: int = 100
    ) -> list[RunLog]:
        stmt = select(RunLog).where(RunLog.run_id == run_id)
        if level:
            stmt = stmt.where(RunLog.level == level)
        stmt = stmt.order_by(RunLog.timestamp.desc()).limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_node_execution(self, run_id: str, node_id: str) -> NodeExecution | None:
        stmt = select(NodeExecution).where(
            NodeExecution.run_id == run_id,
            NodeExecution.node_id == node_id,
        ).order_by(NodeExecution.started_at.desc()).limit(1)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_node_executions(self, run_id: str) -> list[NodeExecution]:
        stmt = select(NodeExecution).where(
            NodeExecution.run_id == run_id
        ).order_by(NodeExecution.started_at.asc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def clear_node_executions(self, run_id: str) -> None:
        """Delete all node execution records for a run (used before re-run)."""
        stmt = delete(NodeExecution).where(NodeExecution.run_id == run_id)
        await self._db.execute(stmt)
        await self._db.commit()

    async def delete_run(self, run_id: str) -> bool:
        """Delete a run, its related records, and its workspace directory."""
        from sqlalchemy import delete as sa_delete

        run = await self.get_run(run_id)
        if run is None:
            return False

        # Resolve work_dir before deleting the DB record
        import json, shutil, os as _os
        ctx = json.loads(run.context) if run.context else {}
        work_dir = ctx.get("run", {}).get("work_dir", "")

        await self._db.execute(sa_delete(RunLog).where(RunLog.run_id == run_id))
        await self._db.execute(delete(NodeExecution).where(NodeExecution.run_id == run_id))
        await self._db.delete(run)
        await self._db.commit()

        # Clean up workspace directory
        if work_dir and _os.path.isdir(work_dir):
            try:
                shutil.rmtree(work_dir)
            except OSError:
                pass

        return True
