"""Workflow CRUD service."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.common.time_utils import utc_now_iso
from wflow.models.db import Workflow


class WorkflowService:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, name: str, config: dict[str, Any], description: str = "") -> Workflow:
        wf = Workflow(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            config=json.dumps(config, ensure_ascii=False),
        )
        self._db.add(wf)
        await self._db.commit()
        return wf

    async def list_all(self, status: str | None = None) -> list[Workflow]:
        stmt = select(Workflow)
        if status:
            stmt = stmt.where(Workflow.status == status)
        stmt = stmt.order_by(Workflow.created_at.desc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, workflow_id: str) -> Workflow | None:
        stmt = select(Workflow).where(Workflow.id == workflow_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Workflow | None:
        stmt = select(Workflow).where(Workflow.name == name).limit(1)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, workflow_id: str, **kwargs: Any) -> Workflow | None:
        wf = await self.get(workflow_id)
        if wf is None:
            return None
        for key, value in kwargs.items():
            if key == "config" and isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            if hasattr(wf, key):
                setattr(wf, key, value)
        wf.updated_at = utc_now_iso()
        await self._db.commit()
        return wf

    async def delete(self, workflow_id: str) -> bool:
        wf = await self.get(workflow_id)
        if wf is None:
            return False
        await self._db.delete(wf)
        await self._db.commit()
        return True
