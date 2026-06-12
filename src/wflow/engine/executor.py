"""Topological workflow executor — drives node-by-node execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.adapters.claude_cli import ClaudeCLIError, ClaudeCLITimeout
from wflow.adapters.opencode_cli import OpenCodeError, OpenCodeTimeout
from wflow.adapters.script_runner import ScriptError
from wflow.common.time_utils import utc_now_iso
from wflow.common.workspace import link_project_dirs
from wflow.engine.node_runner import NodeRunner
from wflow.engine.session_manager import SessionManager
from wflow.engine.state_machine import RunStatus, RunStateMachine
from wflow.engine.template import resolve_template, TemplateContext
from wflow.models.db import NodeExecution, RunLog, WorkflowRun
from wflow.models.workflow import WorkflowSpec

_RUNS_DIR = os.environ.get("WFLOW_RUNS_DIR", "./workspace")
_MAX_ITERATIONS = 1000


# ══════════════════════════════════════════════════════════════════════════════
# Module-level helpers
# ══════════════════════════════════════════════════════════════════════════════


def _normalize_bool(value: str) -> bool | str:
    """Convert common boolean/empty strings to Python bool, or return the original string."""
    v = value.strip().lower()
    if v in ("true", "yes", "1"):
        return True
    if v in ("false", "no", "0", "none", ""):
        return False
    return value.strip()


def _evaluate_condition(condition: str | None, context: TemplateContext) -> bool:
    """Evaluate an edge condition string against context.  No condition → True.

    Supports:
    - Single template variable (resolves to boolean): ``"{{ nodes.x.output.ok }}"``
    - Comparison: ``"{{ nodes.x.output.ok }} == true"`` or ``"!= false"``
    - No condition (None/empty): defaults to True (unconditional edge).
    """
    if not condition:
        return True

    resolved = resolve_template(condition, context)
    if isinstance(resolved, bool):
        return resolved

    if not isinstance(resolved, str):
        resolved = str(resolved)
    resolved = resolved.strip()
    if not resolved:
        return True

    for op_str in ("!=", "=="):
        if op_str in resolved:
            left_str, right_str = resolved.split(op_str, 1)
            left = _normalize_bool(left_str)
            right = _normalize_bool(right_str)
            return left != right if op_str == "!=" else left == right

    return _normalize_bool(resolved) is True


# ══════════════════════════════════════════════════════════════════════════════
# WorkflowExecutor
# ══════════════════════════════════════════════════════════════════════════════


class WorkflowExecutor:
    """Drives execution of a workflow run through its DAG, one node at a time."""

    _SESSION_TYPES: frozenset[str] = frozenset({"claude", "opencode"})

    def __init__(
        self,
        db: AsyncSession,
        node_runner: NodeRunner,
        session_manager: SessionManager,
        logger: logging.Logger | None = None,
        project_dirs: list | None = None,
    ):
        self._db = db
        self._node_runner = node_runner
        self._session_mgr = session_manager
        self._logger = logger
        self._project_dirs = project_dirs or []
        self._state_machine = RunStateMachine()

    # ── Public API ──────────────────────────────────────────────────────────

    async def execute(
        self,
        spec: WorkflowSpec,
        run_id: str,
        context: TemplateContext,
        workflow_name: str = "",
    ) -> bool:
        """Execute the workflow. Returns True on success, False on failure."""
        # --- Work directory setup ---
        work_dir = await self._ensure_work_dir(run_id, context)

        start_ids = self._find_start_nodes(spec)
        if not start_ids:
            self._log(run_id, None, "warn", "No start node found")
            return False

        # --- Resume state ---
        stale_nodes = self._compute_stale_nodes(spec, context)
        if stale_nodes:
            self._log(run_id, None, "info",
                      f"Stale nodes (will re-execute): {sorted(stale_nodes)}")

        previously_completed: set[str] = {
            nid for nid, state in context.get("nodes", {}).items()
            if state.get("status") == "completed" and nid not in stale_nodes
        }
        if previously_completed:
            self._log(run_id, None, "info",
                      f"Previously completed (will skip): {sorted(previously_completed)}")

        # --- Queue-based DAG traversal ---
        ready: list[str] = list(start_ids)
        reached: set[str] = set(start_ids)
        iteration = 0

        while ready and iteration < _MAX_ITERATIONS:
            iteration += 1
            current_node_id = ready.pop(0)

            if not self._all_predecessors_done(spec, current_node_id, context, reached):
                ready.append(current_node_id)
                continue

            node_config = spec.get_node(current_node_id)
            existing_state = context.get("nodes", {}).get(current_node_id)

            # Skip nodes awaiting review
            if existing_state and existing_state.get("status") == "awaiting_review":
                self._log(run_id, current_node_id, "info",
                          f"Skipping node (still awaiting review): {current_node_id}")
                continue

            # Skip previously-completed nodes (non-stale)
            if current_node_id in previously_completed:
                self._log(run_id, current_node_id, "info",
                          f"Skipping node (already completed): {current_node_id}")
                self._enqueue_successors(spec, current_node_id, context, ready, reached)
                continue

            self._log(run_id, current_node_id, "info",
                      f"Executing node: {current_node_id} ({node_config['type']})")

            # --- Execute the node ---
            success = await self._execute_node(
                spec, run_id, current_node_id, node_config, context, work_dir,
            )

            if not success:
                # success=False means either failure or human review pause
                existing = context.get("nodes", {}).get(current_node_id, {})
                if existing.get("status") == "awaiting_review":
                    return False  # graceful pause
                return False  # hard failure

            # Enqueue successors
            self._enqueue_successors(spec, current_node_id, context, ready, reached)

        if iteration >= _MAX_ITERATIONS:
            self._log(run_id, None, "error",
                      "Max iterations reached — possible infinite loop")
            return False

        self._log(run_id, None, "info", "Workflow completed successfully")
        return True

    # ── Work directory ──────────────────────────────────────────────────────

    async def _ensure_work_dir(self, run_id: str, context: TemplateContext) -> str:
        """Create or reuse the working directory for this run.

        Persists the *work_dir* to the DB immediately so the Files API can
        find it even while the workflow is still executing.
        """
        work_dir = context.get("run", {}).get("work_dir")
        if work_dir:
            self._log(run_id, None, "info", f"Using existing work directory: {work_dir}")
            return work_dir

        work_dir = os.path.abspath(
            os.path.join(_RUNS_DIR, f"{run_id[:8]}-{str(uuid.uuid4())[:8]}")
        )
        os.makedirs(work_dir, exist_ok=True)
        context.setdefault("run", {})["work_dir"] = work_dir
        self._log(run_id, None, "info", f"Created work directory: {work_dir}")

        if self._project_dirs:
            link_project_dirs(work_dir, self._project_dirs, self._logger)

        # Persist work_dir immediately so the web UI Files panel works during execution
        try:
            stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
            r = await self._db.execute(stmt)
            bg_run = r.scalar_one_or_none()
            if bg_run is not None:
                bg_run.context = json.dumps(context, ensure_ascii=False)
                await self._db.commit()
        except Exception:
            pass  # DB operation may not be available in test mocks

        return work_dir

    # ── Node execution ──────────────────────────────────────────────────────

    async def _execute_node(
        self,
        spec: WorkflowSpec,
        run_id: str,
        node_id: str,
        node_config: dict[str, Any],
        context: TemplateContext,
        work_dir: str,
    ) -> bool:
        """Execute a single node with all pre/post processing.

        Returns True on success, False on failure or human-review pause.
        """
        # --- Session resolution ---
        session_id, is_resume_session = await self._resolve_session(run_id, node_id, node_config)

        upstream_output = self._get_upstream_output(spec, node_id, context)
        node_input = (
            {"upstream": upstream_output} if upstream_output
            else {"inputs": context.get("inputs", {})}
        )

        ne = NodeExecution(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id=node_id,
            type=node_config["type"],
            session_id=session_id,
            status="running",
            input=json.dumps(node_input, ensure_ascii=False),
        )
        self._db.add(ne)
        await self._db.commit()

        # --- Retry loop ---
        retry_config = node_config.get("retry", {})
        max_retries = retry_config.get("max_retries", 3)
        retry_delay = retry_config.get("retry_delay_seconds", 30)
        last_error: str | None = None

        for attempt in range(max_retries + 1):
            try:
                is_resume = is_resume_session or attempt > 0
                upstream = self._get_upstream_output(spec, node_id, context)
                output = await self._node_runner.run(
                    node_config, context,
                    session_id=session_id,
                    is_resume=is_resume,
                    upstream_output=upstream,
                    cwd=work_dir,
                    retry_reason=last_error,
                )

                # Human review pause
                if output.pop("_awaiting_review", False):
                    return await self._handle_human_review_pause(
                        run_id, node_id, output, context, ne,
                    )

                # Success
                session_id_out = output.pop("_session_id", None)
                context["nodes"][node_id] = {
                    "output": output,
                    "status": "completed",
                    "retry_count": attempt,
                }

                ne.output = json.dumps(output, ensure_ascii=False)
                ne.status = "completed"
                ne.retry_count = attempt
                ne.finished_at = utc_now_iso()
                await self._db.commit()

                # Persist the provider-assigned session ID for future resumes
                if session_id_out and node_config["type"] in self._SESSION_TYPES:
                    await self._session_mgr.set_provider_session_id(
                        run_id, node_id, session_id_out,
                    )
                    await self._session_mgr.mark_completed(session_id_out)

                return True

            except (ClaudeCLIError, ClaudeCLITimeout, OpenCodeError, OpenCodeTimeout, ScriptError) as e:
                last_error = str(e)[:500]
                ne.retry_count = attempt + 1
                ne.error = str(e)[:1000]
                await self._db.commit()

                if attempt >= max_retries:
                    ne.status = "failed"
                    ne.finished_at = utc_now_iso()
                    await self._db.commit()
                    context["nodes"][node_id] = {
                        "output": {}, "status": "failed", "retry_count": attempt,
                    }
                    self._log(run_id, node_id, "error",
                              f"Node failed after {max_retries} retries: {e}")
                    return False

                self._log(run_id, node_id, "warn",
                          f"Retry {attempt + 1}/{max_retries}: {e}")
                await asyncio.sleep(retry_delay)

        return False

    # ── Session resolution ──────────────────────────────────────────────────

    async def _resolve_session(
        self,
        run_id: str,
        node_id: str,
        node_config: dict[str, Any],
    ) -> tuple[str | None, bool]:
        """Determine the session ID and whether this is a resume.

        Returns (session_id, is_resume_session).

        - First run: ``(None, False)`` — adapter runs without ``--session``
          so the provider creates a fresh session whose ID we extract and
          store afterwards.
        - Resume / re-run: ``(provider_sid, True)`` — adapter passes
          ``--session <provider_sid>`` to reuse the existing session.

        ``session_id`` is ``None`` for non-session-based node types
        (script, human_review).
        """
        if node_config["type"] not in self._SESSION_TYPES:
            return None, False

        # Check for a stored provider-assigned session ID (opencode: ses_xxx)
        provider_sid = await self._session_mgr.get_provider_session_id(
            run_id, node_id,
        )
        if provider_sid:
            self._log(run_id, node_id, "info",
                      f"Resuming provider session {provider_sid[:20]}")
            return provider_sid, True

        # No provider session yet — first run (or previous attempt never
        # stored one).  Ensure a tracking record exists.
        await self._session_mgr.get_or_create(run_id, node_id)
        return None, False

    # ── Human review pause ──────────────────────────────────────────────────

    async def _handle_human_review_pause(
        self,
        run_id: str,
        node_id: str,
        output: dict[str, Any],
        context: TemplateContext,
        ne: NodeExecution,
    ) -> bool:
        """Persist the human review pause state and return False (graceful pause)."""
        ne.output = json.dumps(output, ensure_ascii=False)
        ne.status = "awaiting_review"
        ne.finished_at = utc_now_iso()

        context["nodes"][node_id] = {
            "output": output,
            "status": "awaiting_review",
        }

        # Transition run status
        stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
        r = await self._db.execute(stmt)
        bg_run = r.scalar_one_or_none()
        if bg_run:
            bg_run.status = self._state_machine.transition(
                RunStatus(bg_run.status), RunStatus.AWAITING_REVIEW
            ).value
            bg_run.context = json.dumps(context, ensure_ascii=False)

        await self._db.commit()
        self._log(run_id, node_id, "info",
                  "Node awaiting human review — run paused")
        return False

    # ── Graph traversal ─────────────────────────────────────────────────────

    def _compute_stale_nodes(
        self, spec: WorkflowSpec, context: TemplateContext
    ) -> set[str]:
        """Compute which completed nodes must be re-executed due to loop-back.

        A *loop-back edge* has a condition AND its target can reach its source
        through the DAG (via unconditional edges only — see below).  When a
        loop-back edge's condition evaluates to ``True`` and the target is
        already completed, the target (and everything downstream of it) is
        stale and must re-execute.

        Forward conditional edges (target is a genuine successor) never
        trigger staleness — they only control which successor is *next*.
        """
        nodes_ctx = context.get("nodes", {})

        # ── Build ancestor map using ONLY unconditional edges ──────────
        # If the graph has an unconditional path from A to B, then A is an
        # *ancestor* of B.  A conditional edge A → B is a loop-back iff
        # B is an ancestor of A (i.e. B → … → A via unconditional edges).
        ancestor_of: dict[str, set[str]] = {}
        for node in spec.nodes:
            nid = node["id"]
            seen: set[str] = set()
            queue = [nid]
            while queue:
                cur = queue.pop(0)
                if cur in seen:
                    continue
                seen.add(cur)
                for e in spec.get_outgoing_edges(cur):
                    # Only follow UNCONDITIONAL edges when computing ancestry
                    if e.get("condition"):
                        continue
                    t = e.get("to")
                    if t and t not in seen:
                        queue.append(t)
            ancestor_of[nid] = seen  # all nodes reachable from nid via unconditional edges

        # Phase 1: find stale roots via activated LOOP-BACK conditional edges
        stale_roots: set[str] = set()
        for nid, state in nodes_ctx.items():
            if state.get("status") != "completed":
                continue
            node_output = state.get("output", {})
            for edge in spec.get_outgoing_edges(nid):
                condition = edge.get("condition")
                if not condition:
                    continue
                target = edge.get("to")
                if target is None or target not in nodes_ctx:
                    continue
                if nodes_ctx[target].get("status") != "completed":
                    continue

                # Loop-back: the target is an ANCESTOR of the source
                # (a path target → … → source exists via unconditional edges).
                is_loopback = nid in ancestor_of.get(target, set())
                eval_result = _evaluate_condition(condition, context)
                self._log(
                    "", None, "debug",
                    f"stale-check: {nid} → {target} | cond={condition[:60]} "
                    f"| nid_output={json.dumps(node_output, ensure_ascii=False)[:100]} "
                    f"| result={eval_result} | loopback={is_loopback}"
                )
                if eval_result and is_loopback:
                    stale_roots.add(target)

        if not stale_roots:
            return set()

        # Phase 2: BFS downstream
        stale: set[str] = set(stale_roots)
        queue: list[str] = list(stale_roots)
        while queue:
            current = queue.pop(0)
            for edge in spec.get_outgoing_edges(current):
                target = edge.get("to")
                if target is not None and target not in stale and target in nodes_ctx:
                    stale.add(target)
                    queue.append(target)

        return stale

    def _find_start_nodes(self, spec: WorkflowSpec) -> list[str]:
        """Find ALL nodes with no incoming edges (start nodes)."""
        from_ids = {e["from"] for e in spec.edges if "from" in e}
        to_ids = {e["to"] for e in spec.edges if e.get("to") is not None}
        starts = from_ids - to_ids
        if starts:
            return list(starts)
        if spec.nodes:
            return [spec.nodes[0]["id"]]
        return []

    def _find_next_nodes(
        self, spec: WorkflowSpec, current_node_id: str, context: TemplateContext
    ) -> list[str | None]:
        """Return ALL successor nodes for the given node.

        Evaluates conditional edges; falls back to unconditional defaults.
        """
        outgoing = spec.get_outgoing_edges(current_node_id)
        if not outgoing:
            return []

        results: list[str | None] = []
        defaults: list[str | None] = []

        for edge in outgoing:
            if not edge.get("condition"):
                defaults.append(edge.get("to"))
            elif _evaluate_condition(edge["condition"], context):
                results.append(edge.get("to"))

        return results if results else defaults

    def _enqueue_successors(
        self,
        spec: WorkflowSpec,
        node_id: str,
        context: TemplateContext,
        ready: list[str],
        reached: set[str],
    ) -> None:
        """Append successors of *node_id* to the ready queue."""
        for next_id in self._find_next_nodes(spec, node_id, context):
            if next_id is not None and next_id not in ready:
                ready.append(next_id)
                reached.add(next_id)

    def _all_predecessors_done(
        self,
        spec: WorkflowSpec,
        node_id: str,
        context: TemplateContext,
        reached: set[str],
    ) -> bool:
        """Check if all reached predecessors have completed.

        - Unreached predecessor → OK (blocked on this node).
        - Reached but not completed → wait.
        - Loop-back (node already executed): any completed predecessor is enough.
        """
        incoming = [e["from"] for e in spec.edges if e.get("to") == node_id]
        if not incoming:
            return True

        nodes = context.get("nodes", {})

        # Loop-back: re-entrant node — any completed predecessor is sufficient
        if node_id in nodes:
            return any(
                nodes[p].get("status") == "completed"
                for p in incoming if p in nodes
            )

        # First-time: ALL reached predecessors must be completed
        reached_preds = [p for p in incoming if p in reached]
        if not reached_preds:
            return True  # start node

        return all(nodes.get(p, {}).get("status") == "completed" for p in reached_preds)

    def _get_upstream_output(
        self,
        spec: WorkflowSpec,
        node_id: str,
        context: TemplateContext,
    ) -> dict[str, Any] | None:
        """Get completed predecessor outputs for *node_id*.

        - None if first node (no completed predecessors).
        - Single predecessor: returns its output dict directly.
        - Multiple predecessors: returns a dict keyed by predecessor node_id.
        """
        incoming_edges = [e for e in spec.edges if e.get("to") == node_id]
        outputs: dict[str, Any] = {}
        for edge in incoming_edges:
            from_node = edge["from"]
            node_data = context.get("nodes", {}).get(from_node)
            if node_data and node_data.get("status") == "completed":
                outputs[from_node] = node_data.get("output", {})

        if not outputs:
            return None
        if len(outputs) == 1:
            return next(iter(outputs.values()))
        return outputs

    # ── Logging ─────────────────────────────────────────────────────────────

    def _log(self, run_id: str, node_id: str | None, level: str, message: str) -> None:
        log = RunLog(run_id=run_id, level=level, message=message, node_id=node_id)
        self._db.add(log)
        if self._logger:
            log_fn = getattr(self._logger, level, self._logger.info)
            prefix = f"[{node_id}] " if node_id else ""
            log_fn(f"{prefix}{message}")
