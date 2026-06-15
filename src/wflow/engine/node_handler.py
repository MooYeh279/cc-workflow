"""Node type handlers — abstract base and concrete implementations.

Each handler encapsulates the execution logic for one workflow node type
(claude, opencode, script, human_review).  New node types are added by
subclassing :class:`NodeHandler` and registering the handler in the
``handlers`` dict — no changes to :class:`NodeRunner` or the executor
are required.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from wflow.adapters.claude_cli import ClaudeCLI
from wflow.adapters.opencode_cli import OpenCodeCLI
from wflow.adapters.script_runner import ScriptRunner
from wflow.engine.template import TemplateContext


# ══════════════════════════════════════════════════════════════════════════════
# Shared prompt utilities
# ══════════════════════════════════════════════════════════════════════════════


def build_agent_prompt(
    node: dict[str, Any],
    context: TemplateContext,
    upstream_output: dict[str, Any] | None,
    retry_reason: str | None = None,
) -> str:
    """Build the full agent prompt with auto-injected context.

    First node (upstream_output is None): injects user inputs.
    Subsequent nodes: injects upstream node output as JSON.
    Retry: appends error feedback so the agent can correct its output.
    """
    parts = [f"## 角色任务\n{node['prompt']}"]

    if retry_reason:
        parts.append(
            f"\n## 上次输出错误\n{retry_reason}\n"
            f"请修正后重新输出符合 schema 的 JSON 结果。"
        )

    if upstream_output is not None and upstream_output:
        parts.append(
            f"\n## 上游节点输出\n"
            f"{json.dumps(upstream_output, ensure_ascii=False)}"
        )
    elif context.get("inputs"):
        parts.append(
            f"\n## 用户输入\n"
            f"{json.dumps(context['inputs'], ensure_ascii=False)}"
        )

    return "\n\n".join(parts)


def append_schema_instruction(prompt: str, output_schema: dict[str, Any]) -> str:
    """Prepend JSON output format instructions to the prompt."""
    schema_str = json.dumps(output_schema, ensure_ascii=False)
    return (
        "CRITICAL OUTPUT FORMAT -- YOU MUST FOLLOW THIS EXACTLY:\n"
        "1. Use tools to complete the task first (write files, run commands, etc.).\n"
        "2. After all tool work is done, output a SINGLE ```json code block with your result.\n"
        "3. The JSON MUST match this schema exactly:\n"
        f"```json\n{schema_str}\n```\n"
        "4. Do NOT output any text before or after the JSON block -- ONLY the ```json block.\n"
        "5. Do NOT ask questions, explain your thinking, or summarize -- ONLY output the JSON.\n\n"
        "---\n\n"
        + prompt
    )


# ══════════════════════════════════════════════════════════════════════════════
# Abstract base
# ══════════════════════════════════════════════════════════════════════════════


class NodeHandler(ABC):
    """Abstract base for all workflow node type handlers.

    Subclasses encapsulate the execution logic for a specific node type
    (claude, opencode, script, human_review).  To add a new node type,
    subclass :class:`NodeHandler`, implement :meth:`run`, and register
    the instance in the ``handlers`` dict passed to :class:`NodeRunner`.
    """

    @abstractmethod
    async def run(
        self,
        node_config: dict[str, Any],
        context: TemplateContext,
        session_id: str | None,
        is_resume: bool,
        upstream_output: dict[str, Any] | None,
        cwd: str | None,
        retry_reason: str | None,
        logger: Any = None,
    ) -> dict[str, Any]:
        """Execute the node and return its output dict.

        Args:
            node_config: The node definition from the workflow spec.
            context: Template rendering context (inputs, nodes, run, config).
            session_id: Provider-assigned session ID for resume (None on first run).
            is_resume: True when continuing an existing session.
            upstream_output: Output dict(s) from predecessor node(s).
            cwd: Working directory for this run.
            retry_reason: Error feedback from a previous failed attempt.
            logger: Optional logger instance.

        Returns:
            Output dict.  May include ``_awaiting_review: True`` to pause
            execution (human_review node), or ``_session_id`` to persist
            the provider-assigned session for future resumes.
        """
        ...


# ══════════════════════════════════════════════════════════════════════════════
# Session-based handler (shared by claude / opencode)
# ══════════════════════════════════════════════════════════════════════════════


class _SessionNodeHandler(NodeHandler):
    """Base for session-based adapters (claude, opencode).

    Handles prompt building and schema instruction injection shared by
    all LLM-agent node types.  Subclasses provide adapter-specific kwargs
    via :meth:`_extra_kwargs`.
    """

    def __init__(self, adapter: ClaudeCLI | OpenCodeCLI):
        self._adapter = adapter

    async def run(
        self,
        node_config: dict[str, Any],
        context: TemplateContext,
        session_id: str | None,
        is_resume: bool,
        upstream_output: dict[str, Any] | None,
        cwd: str | None,
        retry_reason: str | None,
        logger: Any = None,
    ) -> dict[str, Any]:
        prompt = build_agent_prompt(node_config, context, upstream_output, retry_reason)
        prompt = append_schema_instruction(prompt, node_config["output"])

        kwargs: dict[str, Any] = dict(
            prompt=prompt,
            node_id=node_config["id"],
            session_id=session_id,
            is_resume=is_resume,
            model=node_config.get("model"),
            timeout_seconds=node_config.get("retry", {}).get("timeout_seconds", 1800),
            cwd=cwd,
            logger=logger,
        )
        kwargs.update(self._extra_kwargs(node_config))
        return await self._adapter.run(**kwargs)

    def _extra_kwargs(self, node_config: dict[str, Any]) -> dict[str, Any]:
        """Override in subclasses to inject adapter-specific parameters."""
        return {}


class ClaudeHandler(_SessionNodeHandler):
    """Handler for ``claude`` / ``agent`` node type."""

    def __init__(self, claude: ClaudeCLI):
        super().__init__(claude)

    def _extra_kwargs(self, node_config: dict[str, Any]) -> dict[str, Any]:
        tools = node_config.get("tools", {})
        return {
            "tools_allowed": tools.get("allowed"),
            "tools_disallowed": tools.get("disallowed"),
        }


class OpenCodeHandler(_SessionNodeHandler):
    """Handler for ``opencode`` node type."""

    def __init__(self, opencode: OpenCodeCLI):
        super().__init__(opencode)


# ══════════════════════════════════════════════════════════════════════════════
# Script handler
# ══════════════════════════════════════════════════════════════════════════════


class ScriptHandler(NodeHandler):
    """Handler for ``script`` node type.

    Executes an external command via :class:`ScriptRunner`, passing
    context as JSON on stdin and parsing the JSON result from stdout.
    """

    def __init__(self, script_runner: ScriptRunner):
        self._script = script_runner

    async def run(
        self,
        node_config: dict[str, Any],
        context: TemplateContext,
        session_id: str | None,
        is_resume: bool,
        upstream_output: dict[str, Any] | None,
        cwd: str | None,
        retry_reason: str | None,
        logger: Any = None,
    ) -> dict[str, Any]:
        command = node_config["command"]
        timeout = node_config.get("timeout_seconds", 300)

        script_context: dict[str, Any] = {
            "inputs": context.get("inputs", {}),
            "upstream": upstream_output or {},
            "nodes": {
                k: {"output": v.get("output", {}), "status": v.get("status")}
                for k, v in context.get("nodes", {}).items()
            },
            "run": context.get("run", {}),
            "config": context.get("config", {}),
        }

        return await self._script.run(
            command=command,
            context=script_context,
            timeout_seconds=timeout,
            cwd=cwd,
            logger=logger,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Human review handler
# ══════════════════════════════════════════════════════════════════════════════


class HumanReviewHandler(NodeHandler):
    """Handler for ``human_review`` node type.

    On first call: stores the upstream output for review and returns
    ``_awaiting_review: True`` to pause execution.

    Subsequent calls after an approved review: returns the cached result.
    Rejected review on loop-back: creates a fresh review for the revised
    upstream output.
    """

    # No adapter needed — human_review is fully inline.
    def __init__(self):
        pass

    async def run(
        self,
        node_config: dict[str, Any],
        context: TemplateContext,
        session_id: str | None,
        is_resume: bool,
        upstream_output: dict[str, Any] | None,
        cwd: str | None,
        retry_reason: str | None,
        logger: Any = None,
    ) -> dict[str, Any]:
        node_id = node_config["id"]
        existing = context.get("nodes", {}).get(node_id)

        if existing and existing.get("status") == "completed":
            old_output = existing.get("output", {})
            if old_output.get("approved"):
                return old_output
            # Rejected — fresh review for revised upstream
            if logger:
                logger.info(
                    f"[human_review] {node_id} — previous review was rejected, "
                    f"creating fresh review for revised output"
                )

        upstream_snapshot: dict[str, Any] = (
            {"upstream": upstream_output} if upstream_output
            else {"inputs": context.get("inputs", {})}
        )

        if logger:
            logger.info(f"[human_review] {node_id} — awaiting human review")

        return {
            "_awaiting_review": True,
            "upstream_for_review": upstream_snapshot,
        }
