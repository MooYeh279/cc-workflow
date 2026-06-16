"""Shared workflow execution launcher — used by REST API and cron scheduler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.common.time_utils import utc_now_iso
from wflow.engine.executor import WorkflowExecutor, _resolve_max_concurrency
from wflow.engine.node_runner import NodeRunner
from wflow.engine.session_manager import SessionManager
from wflow.engine.state_machine import RunStatus, RunStateMachine
from wflow.models.db import WorkflowRun, CronJob
from wflow.models.workflow import WorkflowSpec
from wflow.services.execution_svc import ExecutionService
from wflow.services.workflow_svc import WorkflowService

# Track background tasks per run so we can cancel a stale task when a
# newer launch (e.g. a second human review) supersedes it.
_run_tasks: dict[str, asyncio.Task] = {}


def _cancel_stale_task(run_id: str, logger: Any | None = None) -> None:
    """Cancel a previously-launched background task for *run_id* if it
    is still running.  No-op when no task exists or the task is already done.
    """
    old = _run_tasks.get(run_id)
    if old is None or old.done():
        return
    old.cancel()
    if logger:
        logger.info(f"Cancelled stale background task for run {run_id}")


async def start_and_launch(
    *,
    db: AsyncSession,
    workflow_id: str,
    inputs: dict[str, Any],
    handlers: dict[str, Any],
    session_factory: Any,
    wflow_dir: Path | None = None,
    cron_job_id: str | None = None,
    resume_nodes: dict[str, Any] | None = None,
) -> WorkflowRun:
    """Create a run record, set status to RUNNING, and launch execution.

    Used by both the REST API (manual runs, cron triggers, re-runs) and the
    cron scheduler — this is the single place where runs are born.
    """
    svc = ExecutionService(db)
    run = await svc.start_run(workflow_id, inputs)

    sm = RunStateMachine()
    run.status = sm.transition(RunStatus(run.status), RunStatus.RUNNING).value
    await db.commit()

    # Load workflow to get name + config needed by executor
    wf_svc = WorkflowService(db)
    workflow = await wf_svc.get(workflow_id)
    if workflow is None:
        raise ValueError(f"Workflow not found: {workflow_id}")

    # Record last_run_id on the parent cron job so the UI can link back
    if cron_job_id:
        stmt = select(CronJob).where(CronJob.id == cron_job_id)
        result = await db.execute(stmt)
        cron_job = result.scalar_one_or_none()
        if cron_job:
            cron_job.last_run_id = run.id
            await db.commit()

    launch_workflow(
        run_id=run.id,
        workflow_name=workflow.name,
        workflow_config=json.loads(workflow.config),
        inputs=inputs,
        handlers=handlers,
        session_factory=session_factory,
        wflow_dir=wflow_dir,
        resume_nodes=resume_nodes,
    )
    return run


def launch_workflow(
    *,
    run_id: str,
    workflow_name: str,
    workflow_config: dict[str, Any],
    inputs: dict[str, Any],
    handlers: dict[str, Any],
    session_factory: Any,
    wflow_dir: Path | None = None,
    resume_nodes: dict[str, Any] | None = None,
) -> None:
    """Launch a workflow execution as a background asyncio task.

    Set ``resume_nodes`` to a dict of previously-completed node states
    when resuming a paused/awaiting-review run — the executor will skip
    nodes that are already done.
    """
    from wflow.logging import get_run_logger, close_run_logger

    # Strip 'name' from config — it is passed separately to avoid
    # "multiple values for keyword argument 'name'" when the stored
    # config includes the workflow name at the top level.
    wf_config = {k: v for k, v in workflow_config.items() if k != "name"}
    spec = WorkflowSpec(name=workflow_name, **wf_config)
    context: dict[str, Any] = {
        "inputs": inputs,
        "nodes": {},
        "run": {"id": run_id},
        "config": (
            spec.config.model_dump()
            if hasattr(spec.config, "model_dump")
            else spec.config
        ),
    }

    run_logger = get_run_logger(run_id)
    run_logger.info(f"Workflow: {workflow_name}")
    if resume_nodes:
        context["nodes"] = resume_nodes
        run_logger.info(f"Resuming with {len(resume_nodes)} completed nodes")
    run_logger.info(f"Inputs: {json.dumps(inputs, ensure_ascii=False)}")

    # Cancel any previous background task for this run (e.g. a stale
    # executor launched by an earlier human review that hasn't finished
    # yet).  Only one task per run may write the final result.
    _cancel_stale_task(run_id, run_logger)

    task = asyncio.create_task(
        _execute_background(
            run_id=run_id,
            workflow_name=workflow_name,
            spec=spec,
            context=context,
            handlers=handlers,
            session_factory=session_factory,
            wflow_dir=wflow_dir,
            run_logger=run_logger,
        )
    )
    _run_tasks[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: _run_tasks.pop(rid, None))


async def _execute_background(
    *,
    run_id: str,
    workflow_name: str,
    spec: WorkflowSpec,
    context: dict[str, Any],
    handlers: dict[str, Any],
    session_factory: Any,
    wflow_dir: Path | None,
    run_logger: Any,
) -> None:
    """Background task: restore persisted context, run executor, persist result."""
    from wflow.logging import close_run_logger

    try:
        async with session_factory() as bg_db:
            # Restore persisted context from DB (work_dir, previous node outputs)
            await _merge_persisted_context(bg_db, run_id, context, run_logger)

            node_runner = NodeRunner(handlers=handlers, logger=run_logger)
            session_mgr = SessionManager(bg_db)

            max_conc = _resolve_max_concurrency()
            run_logger.info(
                f"Max concurrency: {'unlimited' if max_conc == 0 else max_conc}"
            )

            executor = WorkflowExecutor(
                db=bg_db,
                node_runner=node_runner,
                session_manager=session_mgr,
                logger=run_logger,
                wflow_dir=wflow_dir,
                session_factory=session_factory,
            )

            success = await executor.execute(spec, run_id, context, workflow_name=workflow_name)
            await _persist_run_result(bg_db, run_id, context, success, run_logger)

    except asyncio.CancelledError:
        run_logger.info("Background task cancelled — superseded by newer launch")
        raise

    except Exception:
        import traceback
        run_logger.error(f"Run failed with exception:\n{traceback.format_exc()}")
        # Persist context even on crash so re-runs can find the work_dir
        try:
            await _persist_run_result(bg_db, run_id, context, False, run_logger)
        except Exception:
            pass
    finally:
        close_run_logger(run_id)


async def _merge_persisted_context(
    db: Any,
    run_id: str,
    context: dict[str, Any],
    logger: Any,
) -> None:
    """Restore work_dir and node state from the persisted DB record."""
    stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
    result = await db.execute(stmt)
    db_run = result.scalar_one_or_none()

    if not db_run or not db_run.context:
        return

    try:
        saved_ctx = json.loads(db_run.context)
    except (json.JSONDecodeError, KeyError):
        return

    # Always restore work_dir — critical for session file discovery on resume
    saved_work_dir = saved_ctx.get("run", {}).get("work_dir")
    if saved_work_dir:
        context.setdefault("run", {})["work_dir"] = saved_work_dir
        logger.info(f"Restored work directory: {saved_work_dir}")

    # Merge saved node states when no explicit resume_nodes were provided
    saved_nodes = saved_ctx.get("nodes", {})
    if saved_nodes and not context.get("nodes"):
        context["nodes"] = saved_nodes


async def _persist_run_result(
    db: Any,
    run_id: str,
    context: dict[str, Any],
    success: bool,
    logger: Any,
) -> None:
    """Write the final run status and context back to the database."""
    stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
    result = await db.execute(stmt)
    bg_run = result.scalar_one_or_none()

    if not bg_run:
        return

    # Preserve AWAITING_REVIEW if set by the executor (human review pause)
    if bg_run.status != RunStatus.AWAITING_REVIEW.value:
        bg_run.status = RunStatus.COMPLETED.value if success else RunStatus.FAILED.value

    if bg_run.status in (RunStatus.COMPLETED.value, RunStatus.FAILED.value):
        bg_run.finished_at = utc_now_iso()

    bg_run.context = json.dumps(context, ensure_ascii=False)
    await db.commit()

    logger.info(
        f"Run finished: status={bg_run.status}, success={success}"
    )
