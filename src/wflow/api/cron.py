"""Cron job management endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.api.deps import get_db, get_adapters
from wflow.services.execution_svc import ExecutionService
from wflow.services.schedule_svc import ScheduleService
from wflow.services.run_launcher import start_and_launch

router = APIRouter(prefix="/cron", tags=["cron"])


class CreateCronRequest(BaseModel):
    workflow_id: str
    cron_expr: str
    inputs: dict = {}


@router.post("", status_code=201)
async def create_cron_job(req: CreateCronRequest, request: Request, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db, scheduler=request.app.state.scheduler)
    try:
        job = await svc.create(req.workflow_id, req.cron_expr, req.inputs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": job.id, "workflow_id": job.workflow_id,
            "cron_expr": job.cron_expr, "enabled": bool(job.enabled),
            "inputs": json.loads(job.inputs) if job.inputs else {},
            "created_at": job.created_at}


@router.get("")
async def list_cron_jobs(request: Request, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    jobs = await svc.list_all()

    # Enrich with workflow names
    from wflow.services.workflow_svc import WorkflowService
    wf_svc = WorkflowService(db)
    wf_names: dict[str, str] = {}
    for j in jobs:
        if j.workflow_id not in wf_names:
            wf = await wf_svc.get(j.workflow_id)
            wf_names[j.workflow_id] = wf.name if wf else ""

    return [{"id": j.id, "workflow_id": j.workflow_id,
             "workflow_name": wf_names.get(j.workflow_id, ""),
             "cron_expr": j.cron_expr,
             "enabled": bool(j.enabled), "last_run_id": j.last_run_id,
             "inputs": json.loads(j.inputs) if j.inputs else {},
             "next_fire_at": j.next_fire_at, "created_at": j.created_at}
            for j in jobs]


@router.get("/{job_id}")
async def get_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Cron job detail including recent runs for the associated workflow."""
    svc = ScheduleService(db)
    job = await svc.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")

    # Load associated workflow
    from wflow.services.workflow_svc import WorkflowService
    wf_svc = WorkflowService(db)
    workflow = await wf_svc.get(job.workflow_id)

    # Load recent runs for this workflow
    exec_svc = ExecutionService(db)
    recent_runs = await exec_svc.list_runs(workflow_id=job.workflow_id, limit=10)

    return {
        "id": job.id, "workflow_id": job.workflow_id,
        "workflow_name": workflow.name if workflow else "",
        "cron_expr": job.cron_expr,
        "enabled": bool(job.enabled),
        "last_run_id": job.last_run_id,
        "inputs": json.loads(job.inputs) if job.inputs else {},
        "created_at": job.created_at,
        "runs": [{"id": r.id, "status": r.status,
                  "started_at": r.started_at, "finished_at": r.finished_at}
                 for r in recent_runs],
    }


@router.put("/{job_id}")
async def update_cron_job(job_id: str, req: CreateCronRequest, request: Request, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db, scheduler=request.app.state.scheduler)
    job = await svc.update(job_id, cron_expr=req.cron_expr, inputs=json.dumps(req.inputs, ensure_ascii=False))
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    return {"id": job.id, "cron_expr": job.cron_expr}


@router.delete("/{job_id}", status_code=204)
async def delete_cron_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db, scheduler=request.app.state.scheduler)
    deleted = await svc.delete(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cron job not found")


@router.post("/{job_id}/toggle")
async def toggle_cron_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db, scheduler=request.app.state.scheduler)
    job = await svc.toggle(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    return {"id": job.id, "enabled": bool(job.enabled)}


@router.post("/{job_id}/trigger")
async def trigger_cron_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    job = await svc.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")

    inputs = json.loads(job.inputs) if job.inputs else {}
    adapters = get_adapters(request)
    run = await start_and_launch(
        db=db,
        workflow_id=job.workflow_id,
        inputs=inputs,
        cron_job_id=job_id,
        **adapters,
    )
    return {"run_id": run.id, "status": run.status}
