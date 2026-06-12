"""Workflow CRUD endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from wflow.api.deps import get_db
from wflow.logging import get_server_logger
from wflow.services.workflow_svc import WorkflowService

router = APIRouter(prefix="/workflows", tags=["workflows"])
_log = get_server_logger()


class CreateWorkflowRequest(BaseModel):
    name: str
    description: str = ""
    config: dict


@router.post("", status_code=201)
async def create_workflow(req: CreateWorkflowRequest, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    existing = await svc.get_by_name(req.name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Workflow with name '{req.name}' already exists (id={existing.id[:8]})",
        )
    wf = await svc.create(name=req.name, config=req.config, description=req.description)
    _log.info(f"Workflow created: {wf.id[:8]} name='{wf.name}' nodes={len(req.config.get('nodes', []))}")
    return {
        "id": wf.id, "name": wf.name, "description": wf.description,
        "config": json.loads(wf.config), "status": wf.status,
        "created_at": wf.created_at, "updated_at": wf.updated_at,
    }


@router.get("")
async def list_workflows(status: str | None = None, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    workflows = await svc.list_all(status=status)
    return [{"id": w.id, "name": w.name, "description": w.description,
             "status": w.status, "created_at": w.created_at} for w in workflows]


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    wf = await svc.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"id": wf.id, "name": wf.name, "description": wf.description,
            "config": json.loads(wf.config), "status": wf.status,
            "created_at": wf.created_at, "updated_at": wf.updated_at}


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, req: CreateWorkflowRequest, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    wf = await svc.update(workflow_id, name=req.name, config=req.config, description=req.description)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"id": wf.id, "name": wf.name, "status": wf.status}


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    deleted = await svc.delete(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workflow not found")
