"""Status and health-check endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.api.deps import get_db
from wflow.models.db import Session, WorkflowRun

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status(db: AsyncSession = Depends(get_db)):
    running = await db.execute(
        select(func.count())
        .select_from(WorkflowRun)
        .where(WorkflowRun.status == "running")
    )
    running_count = running.scalar() or 0

    awaiting = await db.execute(
        select(func.count())
        .select_from(WorkflowRun)
        .where(WorkflowRun.status == "awaiting_review")
    )
    awaiting_count = awaiting.scalar() or 0

    completed = await db.execute(
        select(func.count())
        .select_from(WorkflowRun)
        .where(WorkflowRun.status == "completed")
    )
    completed_count = completed.scalar() or 0

    failed = await db.execute(
        select(func.count())
        .select_from(WorkflowRun)
        .where(WorkflowRun.status == "failed")
    )
    failed_count = failed.scalar() or 0

    return {
        "status": "ok",
        "running_workflows": running_count,
        "awaiting_review": awaiting_count,
        "completed_workflows": completed_count,
        "failed_workflows": failed_count,
    }


@router.get("/status/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    stmt = select(Session).where(Session.status == "active").limit(100)
    result = await db.execute(stmt)
    sessions = result.scalars().all()
    return [
        {
            "id": s.id,
            "run_id": s.run_id,
            "node_id": s.node_id,
            "status": s.status,
            "created_at": s.created_at,
        }
        for s in sessions
    ]
