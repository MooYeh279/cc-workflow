"""Run management endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from wflow.api.deps import get_db, get_adapters
from wflow.common.time_utils import utc_now_iso
from wflow.engine.state_machine import RunStatus, RunStateMachine
from wflow.models.db import WorkflowRun
from wflow.services.execution_svc import ExecutionService
from wflow.services.workflow_svc import WorkflowService
from wflow.services.run_launcher import start_and_launch, launch_workflow

router = APIRouter(prefix="/runs", tags=["runs"])


class StartRunRequest(BaseModel):
    workflow_id: str
    inputs: dict = {}


class ReviewRequest(BaseModel):
    approved: bool
    feedback: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_workflow_or_404(db: AsyncSession, workflow_id: str) -> Any:
    """Load a workflow by ID, or raise 404."""
    wf_svc = WorkflowService(db)
    workflow = await wf_svc.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


def _launch(
    *,
    run_id: str,
    workflow: Any,
    inputs: dict[str, Any],
    adapters: dict[str, Any],
    resume_nodes: dict[str, Any] | None = None,
) -> None:
    """Launch a workflow run with the standard boilerplate."""
    launch_workflow(
        run_id=run_id,
        workflow_name=workflow.name,
        workflow_config=json.loads(workflow.config),
        inputs=inputs,
        resume_nodes=resume_nodes,
        **adapters,
    )


@router.post("", status_code=201)
async def start_run(req: StartRunRequest, request: Request, db: AsyncSession = Depends(get_db)):
    run = await start_and_launch(
        db=db, workflow_id=req.workflow_id, inputs=req.inputs,
        **get_adapters(request),
    )
    return {
        "id": run.id, "workflow_id": run.workflow_id,
        "status": run.status, "current_node_id": run.current_node_id,
        "started_at": run.started_at,
    }


@router.get("")
async def list_runs(workflow_id: str | None = None, status: str | None = None,
                    limit: int = 50, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    runs = await svc.list_runs(workflow_id=workflow_id, status=status, limit=limit)

    # Build workflow_id → name map for display
    wf_ids = {r.workflow_id for r in runs}
    wf_svc = WorkflowService(db)
    wf_names: dict[str, str] = {}
    for wid in wf_ids:
        wf = await wf_svc.get(wid)
        if wf:
            wf_names[wid] = wf.name

    return [{"id": r.id, "workflow_id": r.workflow_id,
             "workflow_name": wf_names.get(r.workflow_id, ""),
             "status": r.status,
             "current_node_id": r.current_node_id, "started_at": r.started_at,
             "finished_at": r.finished_at} for r in runs]


@router.get("/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    node_executions = await svc.get_node_executions(run_id)

    # Extract work_dir from context
    ctx = json.loads(run.context) if run.context else {}
    work_dir = ctx.get("run", {}).get("work_dir", "")

    # Load workflow spec for DAG structure
    wf_svc = WorkflowService(db)
    workflow = await wf_svc.get(run.workflow_id)
    spec_nodes = []
    spec_edges = []
    if workflow:
        try:
            wf_config = json.loads(workflow.config)
            spec_nodes = wf_config.get("nodes", [])
            spec_edges = wf_config.get("edges", [])
        except (json.JSONDecodeError, KeyError) as e:
            import logging
            logging.getLogger("wflow.api").warning(
                "Failed to parse workflow config for run %s: %s", run_id, e
            )

    return {
        "id": run.id, "workflow_id": run.workflow_id, "status": run.status,
        "current_node_id": run.current_node_id, "work_dir": work_dir,
        "started_at": run.started_at, "finished_at": run.finished_at,
        "nodes": [{"id": ne.id, "node_id": ne.node_id, "type": ne.type,
                   "status": ne.status, "session_id": ne.session_id,
                   "retry_count": ne.retry_count,
                   "input": ne.input, "output": ne.output, "error": ne.error}
                  for ne in node_executions],
        "spec": {"nodes": spec_nodes, "edges": spec_edges},
    }


@router.post("/{run_id}/pause")
async def pause_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.pause_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": run.id, "status": run.status}


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.resume_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": run.id, "status": run.status}


@router.post("/{run_id}/stop")
async def stop_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.stop_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": run.id, "status": run.status}


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    deleted = await svc.delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")


@router.post("/{run_id}/rerun", status_code=200)
async def rerun_run(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Re-run a failed or completed run — restarts the SAME run in-place.

    Resets status to RUNNING, clears node execution history, and re-launches
    the executor with the original inputs.  No new run record is created.
    """
    svc = ExecutionService(db, engine=None)
    run = await svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    ctx = json.loads(run.context) if run.context else {}
    inputs = ctx.get("inputs", {})
    workflow = await _get_workflow_or_404(db, run.workflow_id)

    # Rerun is a force restart — accept any rewinding state
    sm = RunStateMachine()
    current = RunStatus(run.status)
    if sm.can_transition(current, RunStatus.RUNNING):
        run.status = sm.transition(current, RunStatus.RUNNING).value
    elif current == RunStatus.RUNNING:
        pass  # orphaned run, already running — just reset
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot rerun from '{current.value}'",
        )
    run.current_node_id = None
    run.finished_at = None
    ctx["nodes"] = {}
    run.context = json.dumps(ctx, ensure_ascii=False)
    await svc.clear_node_executions(run_id)
    await db.commit()

    _launch(
        run_id=run.id, workflow=workflow, inputs=inputs,
        adapters=get_adapters(request),
    )

    return {
        "id": run.id, "workflow_id": run.workflow_id,
        "status": run.status, "current_node_id": run.current_node_id,
        "started_at": run.started_at,
    }


@router.get("/{run_id}/logs")
async def get_logs(run_id: str, level: str | None = None, limit: int = 100,
                   db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    logs = await svc.get_logs(run_id, level=level, limit=limit)
    return [{"id": l.id, "node_id": l.node_id, "level": l.level,
             "message": l.message, "timestamp": l.timestamp} for l in logs]


@router.get("/{run_id}/nodes/{node_id}")
async def get_node_execution(run_id: str, node_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    ne = await svc.get_node_execution(run_id, node_id)
    if ne is None:
        raise HTTPException(status_code=404, detail="Node execution not found")
    return {"id": ne.id, "node_id": ne.node_id, "type": ne.type,
            "status": ne.status, "session_id": ne.session_id,
            "retry_count": ne.retry_count, "input": ne.input,
            "output": ne.output, "error": ne.error,
            "started_at": ne.started_at, "finished_at": ne.finished_at}


# ── File Browser ───────────────────────────────────────────────────────────

async def _resolve_work_dir(run_id: str, db: AsyncSession) -> Path | None:
    """Resolve the work directory for a run.  Returns ``None`` when the
    directory is not available (run still initialising or finished)."""
    stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        return None

    ctx = json.loads(run.context) if run.context else {}
    work_dir = ctx.get("run", {}).get("work_dir", "")
    if not work_dir:
        return None

    wd = Path(work_dir)
    if not wd.exists():
        return None
    return wd.resolve()


@router.get("/{run_id}/files")
async def list_workdir_files(
    run_id: str,
    path: str = Query(default="", description="Relative path within work directory"),
    db: AsyncSession = Depends(get_db),
):
    """List files and directories inside the run's work directory."""
    wd = await _resolve_work_dir(run_id, db)
    if wd is None:
        return {"work_dir": "", "path": ".", "entries": []}

    # Security: traverse-check the UNRESOLVED path (before symlinks are
    # followed) so that symlinked directories (e.g .wflow, skills, agents)
    # are accessible without triggering path-traversal denial.
    target_unresolved = (wd / path) if path else wd
    try:
        target_unresolved.relative_to(wd)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    # Now resolve symlinks for filesystem operations.
    target = target_unresolved.resolve()
    if not target.exists() or not target.is_dir():
        return {"work_dir": str(wd), "path": path or ".", "entries": []}

    entries = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            # Build relative path from the unresolved location so that
            # entries inside symlinked directories (e.g. .wflow/skills)
            # still compute correctly via relative_to(wd).
            entry_unresolved = target_unresolved / entry.name
            rel = str(entry_unresolved.relative_to(wd)).replace("\\", "/")
            entries.append({
                "name": entry.name,
                "path": rel,
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {"work_dir": str(wd), "path": path or ".", "entries": entries}


@router.get("/{run_id}/files/content")
async def read_workdir_file(
    run_id: str,
    path: str = Query(..., description="Relative file path within work directory"),
    db: AsyncSession = Depends(get_db),
):
    """Read the content of a file inside the run's work directory."""
    wd = await _resolve_work_dir(run_id, db)
    if wd is None:
        raise HTTPException(status_code=404, detail="Work directory not available")

    target_unresolved = wd / path
    try:
        target_unresolved.relative_to(wd)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    target = target_unresolved.resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    if target.stat().st_size > 500 * 1024:
        raise HTTPException(status_code=400, detail="File too large (>500KB)")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = target.read_text(encoding="latin-1", errors="replace")

    return {"path": path, "size": target.stat().st_size, "content": content}


# ── Human Review ────────────────────────────────────────────────────────────

@router.post("/{run_id}/nodes/{node_id}/review")
async def submit_human_review(
    run_id: str,
    node_id: str,
    req: ReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Submit a human review decision for a human_review node.

    - If approved: the upstream output is passed through as the node output.
    - If rejected: feedback is returned, and the executor will route via
      the ``approved == false`` loop-back edge.
    """
    svc = ExecutionService(db, engine=None)
    ne = await svc.get_node_execution(run_id, node_id)
    if ne is None:
        raise HTTPException(status_code=404, detail="Node execution not found")
    if ne.status != "awaiting_review":
        raise HTTPException(status_code=400, detail=f"Node is not awaiting review (status={ne.status})")

    run = await svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Update run context
    ctx = json.loads(run.context) if run.context else {}

    # When approved: merge the UPSTREAM node's actual output into the review
    # result so the next node receives the work product.  The top-level
    # ``approved`` / ``feedback`` keys are preserved for condition evaluation.
    if req.approved:
        current_output = ctx.get("nodes", {}).get(node_id, {}).get("output", {})
        upstream_for_review = current_output.get("upstream_for_review", {})
        # upstream_for_review is {"upstream": {...actual work...}} or {"inputs": {...}}
        upstream = upstream_for_review.get("upstream") or upstream_for_review.get("inputs", {})
        output = {
            "approved": True,
            "feedback": req.feedback or "",
        }
        # Merge upstream work UNDER the review decision (so template vars
        # like ``approved`` / ``feedback`` still work).
        if isinstance(upstream, dict):
            for k, v in upstream.items():
                if k not in output:
                    output[k] = v
    else:
        output = {"approved": False, "feedback": req.feedback}

    ne.output = json.dumps(output, ensure_ascii=False)
    ne.status = "completed"
    ne.finished_at = utc_now_iso()
    await db.commit()

    ctx.setdefault("nodes", {})[node_id] = {
        "output": output,
        "status": "completed",
    }
    run.context = json.dumps(ctx, ensure_ascii=False)

    # Transition run back to RUNNING if it isn't already (idempotent:
    # another concurrent review may have already relaunched the executor).
    sm = RunStateMachine()
    current_status = RunStatus(run.status)
    if current_status != RunStatus.RUNNING:
        run.status = sm.transition(current_status, RunStatus.RUNNING).value
    await db.commit()

    workflow = await _get_workflow_or_404(db, run.workflow_id)
    _launch(
        run_id=run.id, workflow=workflow,
        inputs=ctx.get("inputs", {}),
        adapters=get_adapters(request),
        resume_nodes=ctx.get("nodes", {}),
    )

    return {"node_id": node_id, "status": "completed", "approved": req.approved}
