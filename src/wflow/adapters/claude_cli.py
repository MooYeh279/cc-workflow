"""Claude Code CLI adapter — subprocess management with streaming JSON output."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from typing import Any

from wflow.common.json_parser import extract_json, JSONParseError


class ClaudeCLIError(Exception):
    """Raised when Claude CLI returns an error or produces unparseable output."""


class ClaudeCLITimeout(ClaudeCLIError):
    """Raised when Claude CLI exceeds timeout."""


class ClaudeCLI:
    """Wraps Claude Code CLI (``claude``) subprocess calls with streaming JSON logs.

    Uses ``--output-format stream-json --input-format stream-json --verbose``
    to produce structured, parseable streaming events.  The result is extracted
    *during* the stream — no second-pass parsing.
    """

    _HEARTBEAT_SECONDS: float = 30.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_command(
        self,
        session_id: str,
        is_resume: bool,
        tools_allowed: list[str] | None = None,
        tools_disallowed: list[str] | None = None,
        model: str | None = None,
    ) -> list[str]:
        claude_exe = shutil.which("claude") or "claude"
        cmd = [
            claude_exe, "--print", "--verbose",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--include-partial-messages",
        ]
        if is_resume:
            cmd.extend(["--resume", session_id])
        else:
            cmd.extend(["--session-id", session_id])
        if tools_allowed:
            cmd.extend(["--allowedTools", ",".join(tools_allowed)])
        if tools_disallowed:
            cmd.extend(["--disallowedTools", ",".join(tools_disallowed)])
        if model:
            cmd.extend(["--model", model])
        return cmd

    @staticmethod
    def _build_input(prompt: str) -> str:
        return json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        }, ensure_ascii=False) + "\n"

    # ------------------------------------------------------------------
    # Streaming parser — extracts result DURING streaming (one pass)
    # ------------------------------------------------------------------

    async def _stream_and_parse(
        self,
        proc: asyncio.subprocess.Process,
        node_id: str,
        logger: logging.Logger | None,
    ) -> dict[str, Any]:
        """Consume stream-json stdout, log status, return parsed result dict.

        The result is extracted inline — no second-pass parsing over
        accumulated raw text.
        """
        parsed_result: dict[str, Any] | None = None
        fallback_texts: list[str] = []
        event_count = 0
        tool_count = 0
        last_text = ""
        last_heartbeat = time.monotonic()

        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break

            line_str = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            if not line_str:
                continue

            event_count += 1

            try:
                event = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            evt_type = event.get("type", "")
            subtype = event.get("subtype", "")

            # --- system / init -------------------------------------------------
            if evt_type == "system" and subtype == "init":
                if logger:
                    tools = event.get("tools", [])
                    model_name = event.get("model", "?")
                    logger.info(
                        f"[{node_id}] 🔧 Init: model={model_name}, tools={len(tools)}"
                    )
                last_heartbeat = time.monotonic()

            # --- assistant messages (log + collect text fallback) ---------------
            elif evt_type == "assistant":
                msg = event.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "tool_use":
                        tool_count += 1
                        if logger:
                            logger.info(
                                f"[{node_id}] 🔨 {block.get('name', '?')}(%s)"
                                % json.dumps(block.get("input", {}), ensure_ascii=False)[:200]
                            )
                        last_heartbeat = time.monotonic()
                    elif block_type == "text":
                        text = block.get("text", "").strip()
                        if text:
                            fallback_texts.append(text)
                            if logger and text != last_text:
                                last_text = text
                                logger.info(
                                    f"[{node_id}] 💬 {text[:300]}{'...' if len(text) > 300 else ''}"
                                )
                                last_heartbeat = time.monotonic()

            # --- user messages (tool results) ----------------------------------
            elif evt_type == "user":
                msg = event.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            result_text = ""
                            if isinstance(block.get("content"), str):
                                result_text = block["content"]
                            elif isinstance(block.get("content"), list):
                                result_text = "".join(
                                    c.get("text", "") for c in block["content"]
                                    if isinstance(c, dict)
                                )
                            if logger:
                                logger.info(
                                    f"[{node_id}] ✅ tool result ({len(result_text)} chars)"
                                )
                            last_heartbeat = time.monotonic()

            # --- result — extract inline ---------------------------------------
            elif evt_type == "result":
                if logger:
                    logger.info(f"[{node_id}] 🏁 Result received")
                result_value = event.get("result")
                if result_value is not None:
                    if isinstance(result_value, dict):
                        parsed_result = result_value
                    elif isinstance(result_value, str):
                        try:
                            parsed_result = json.loads(result_value)
                        except json.JSONDecodeError:
                            try:
                                parsed_result = extract_json(result_value)
                            except JSONParseError:
                                pass
                last_heartbeat = time.monotonic()

            # --- periodic heartbeat --------------------------------------------
            now = time.monotonic()
            if logger and now - last_heartbeat > self._HEARTBEAT_SECONDS:
                logger.info(
                    f"[{node_id}] ⏳ working… ({event_count} events, "
                    f"{tool_count} tool calls)"
                )
                last_heartbeat = now

        # --- Return result ----------------------------------------------------
        if parsed_result is not None:
            return parsed_result

        combined = "\n".join(fallback_texts)
        if combined.strip():
            try:
                return extract_json(combined)
            except JSONParseError as e:
                raise ClaudeCLIError(str(e))

        raise ClaudeCLIError("No result found in stream output")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        node_id: str,
        session_id: str | None,
        is_resume: bool,
        output_schema: dict[str, Any],
        tools_allowed: list[str] | None = None,
        tools_disallowed: list[str] | None = None,
        model: str | None = None,
        timeout_seconds: int = 1800,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        sid = session_id or str(uuid.uuid4())
        merged_env = {**os.environ, **(env or {})}

        cmd = self._build_command(
            session_id=sid, is_resume=is_resume,
            tools_allowed=tools_allowed, tools_disallowed=tools_disallowed,
            model=model,
        )
        input_data = self._build_input(prompt)

        if logger:
            logger.info(f"[{node_id}] CMD: {' '.join(cmd)}  cwd={cwd}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            cwd=cwd,
        )

        try:
            if proc.stdin:
                proc.stdin.write(input_data.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()

            parsed = await asyncio.wait_for(
                self._stream_and_parse(proc, node_id, logger),
                timeout=timeout_seconds,
            )

            stderr_bytes = await proc.stderr.read()
            await proc.wait()

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise ClaudeCLITimeout(
                f"Claude CLI timed out after {timeout_seconds}s for node '{node_id}'"
            )

        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        if logger and stderr:
            logger.info(f"[{node_id}] STDERR: {stderr[:300]}")

        if proc.returncode != 0 and proc.returncode is not None:
            raise ClaudeCLIError(
                f"Claude CLI exited with code {proc.returncode} for node '{node_id}'. "
                f"stderr: {stderr[:500]}"
            )

        parsed["_session_id"] = sid
        return parsed
