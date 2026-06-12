"""APScheduler integration for cron-triggered workflows."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.common.time_utils import utc_now_iso
from wflow.models.db import CronJob
from wflow.services.run_launcher import start_and_launch

_logger = logging.getLogger("wflow.scheduler")


class CronJobManager:
    """CRUD operations for cron jobs in SQLite."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(
        self, workflow_id: str, cron_expr: str, inputs: dict[str, Any] | None = None
    ) -> CronJob:
        # Normalize: strip Quartz-style '?' (APScheduler uses standard 5-field Unix cron)
        cron_expr = " ".join(f for f in cron_expr.strip().split() if f != "?")
        job = CronJob(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            cron_expr=cron_expr,
            inputs=json.dumps(inputs or {}, ensure_ascii=False),
            enabled=1,
        )
        self._db.add(job)
        await self._db.commit()
        return job

    async def list_all(self) -> list[CronJob]:
        stmt = select(CronJob).order_by(CronJob.created_at.desc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, job_id: str) -> CronJob | None:
        stmt = select(CronJob).where(CronJob.id == job_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, job_id: str, **kwargs: Any) -> CronJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)
        await self._db.commit()
        return job

    async def toggle(self, job_id: str) -> CronJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.enabled = 1 if job.enabled == 0 else 0
        await self._db.commit()
        return job

    async def delete(self, job_id: str) -> bool:
        stmt = sa_delete(CronJob).where(CronJob.id == job_id)
        result = await self._db.execute(stmt)
        await self._db.commit()
        return result.rowcount > 0

    async def get_enabled(self) -> list[CronJob]:
        stmt = select(CronJob).where(CronJob.enabled == 1)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())


class WorkflowScheduler:
    """Manages APScheduler lifecycle for cron-triggered workflow execution."""

    def __init__(self, session_factory: Any):
        self._scheduler = AsyncIOScheduler()
        self._session_factory = session_factory
        self._handlers: dict[str, Any] = {}
        self._project_dirs: list = []

    def set_adapters(
        self, handlers: dict[str, Any], project_dirs: list | None = None
    ) -> None:
        """Set the node handlers for launching workflow executions."""
        self._handlers = handlers
        self._project_dirs = project_dirs or []

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def add_job(self, cron_job: CronJob) -> str:
        # Normalize: strip Quartz-style '?' (APScheduler uses standard 5-field Unix cron)
        expr = " ".join(f for f in cron_job.cron_expr.strip().split() if f != "?")
        fields = expr.split()
        # APScheduler CronTrigger constructor supports 'second' parameter
        # even though from_crontab() only accepts 5-field expressions.
        # 6-field format: second minute hour day month day_of_week
        if len(fields) == 6:
            trigger = CronTrigger(
                second=fields[0], minute=fields[1], hour=fields[2],
                day=fields[3], month=fields[4], day_of_week=fields[5],
            )
        else:
            trigger = CronTrigger.from_crontab(expr)
        inputs = json.loads(cron_job.inputs) if cron_job.inputs else {}
        aps_job = self._scheduler.add_job(
            self._execute_job,
            trigger=trigger,
            args=[cron_job.workflow_id, inputs, cron_job.id],
            id=f"cron-{cron_job.id}",
            replace_existing=True,
        )
        return aps_job.id

    def remove_job(self, cron_job_id: str) -> None:
        job_key = f"cron-{cron_job_id}"
        try:
            self._scheduler.remove_job(job_key)
        except JobLookupError:
            _logger.warning("Scheduler remove_job: job %s not found (already removed?)", job_key)
        except Exception:
            _logger.exception("Scheduler remove_job failed for %s", job_key)
            raise

    def pause_job(self, cron_job_id: str) -> None:
        job_key = f"cron-{cron_job_id}"
        try:
            self._scheduler.pause_job(job_key)
        except JobLookupError:
            _logger.warning("Scheduler pause_job: job %s not found (already removed?)", job_key)
        except Exception:
            _logger.exception("Scheduler pause_job failed for %s", job_key)
            raise

    def resume_job(self, cron_job_id: str) -> None:
        job_key = f"cron-{cron_job_id}"
        try:
            self._scheduler.resume_job(job_key)
        except JobLookupError:
            _logger.warning("Scheduler resume_job: job %s not found (already removed?)", job_key)
        except Exception:
            _logger.exception("Scheduler resume_job failed for %s", job_key)
            raise

    async def _execute_job(self, workflow_id: str, inputs: dict[str, Any], cron_job_id: str = "") -> None:
        """Execute a scheduled workflow run with its own DB session."""
        async with self._session_factory() as db:
            await start_and_launch(
                db=db,
                workflow_id=workflow_id,
                inputs=inputs,
                cron_job_id=cron_job_id or None,
                handlers=self._handlers,
                session_factory=self._session_factory,
                project_dirs=self._project_dirs,
            )

    async def restore_jobs(self, db_session_factory) -> None:
        """Restore all enabled cron jobs from database on startup."""
        async with db_session_factory() as db:
            mgr = CronJobManager(db)
            jobs = await mgr.get_enabled()
            for job in jobs:
                self.add_job(job)
