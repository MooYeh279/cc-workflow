"""Node runner — dispatches to the appropriate :class:`NodeHandler` for execution."""

from __future__ import annotations

from typing import Any

from wflow.engine.node_handler import NodeHandler
from wflow.engine.template import TemplateContext


class NodeRunner:
    """Executes a single workflow node via registered type handlers.

    Handlers are injected as a ``{type_name: NodeHandler}`` dict, making
    it trivial to add new node types — just register a new handler without
    touching :class:`NodeRunner` or the executor.
    """

    def __init__(
        self,
        handlers: dict[str, NodeHandler],
        logger: Any = None,
    ):
        self._handlers = handlers
        self._logger = logger

    async def run(
        self,
        node_config: dict[str, Any],
        context: TemplateContext,
        session_id: str | None,
        is_resume: bool = False,
        upstream_output: dict[str, Any] | None = None,
        cwd: str | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        """Execute a node and return its output dict."""
        node_type = node_config["type"]

        handler = self._handlers.get(node_type)
        if handler is None:
            raise ValueError(f"Unknown node type: {node_type}")

        return await handler.run(
            node_config, context, session_id, is_resume,
            upstream_output, cwd, retry_reason, logger=self._logger,
        )
