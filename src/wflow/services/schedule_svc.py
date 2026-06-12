"""Schedule service — cron job management with scheduler integration."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wflow.engine.scheduler import CronJobManager, WorkflowScheduler
from wflow.models.db import CronJob


class ScheduleService:
    """CRUD for cron jobs, synced with the in-process scheduler."""

    def __init__(self, db: AsyncSession, scheduler: WorkflowScheduler | None = None):
        self._db = db
        self._mgr = CronJobManager(db)
        self._scheduler = scheduler

    async def create(
        self, workflow_id: str, cron_expr: str, inputs: dict[str, Any] | None = None
    ) -> CronJob:
        job = await self._mgr.create(workflow_id, cron_expr, inputs)
        if self._scheduler and job.enabled:
            self._scheduler.add_job(job)
        return job

    async def list_all(self) -> list[CronJob]:
        return await self._mgr.list_all()

    async def get(self, job_id: str) -> CronJob | None:
        return await self._mgr.get(job_id)

    async def update(self, job_id: str, **kwargs: Any) -> CronJob | None:
        job = await self._mgr.update(job_id, **kwargs)
        if job and self._scheduler:
            self._scheduler.add_job(job)  # add_job uses replace_existing=True
        return job

    async def delete(self, job_id: str) -> bool:
        if self._scheduler:
            self._scheduler.remove_job(job_id)
        return await self._mgr.delete(job_id)

    async def toggle(self, job_id: str) -> CronJob | None:
        job = await self._mgr.toggle(job_id)
        if job is None:
            return None
        if self._scheduler:
            if job.enabled:
                self._scheduler.add_job(job)
            else:
                self._scheduler.pause_job(job_id)
        return job
