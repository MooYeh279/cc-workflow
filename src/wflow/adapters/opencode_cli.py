"""OpenCode CLI adapter — run + export for reliable text extraction."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from typing import Any

from wflow.common.json_parser import extract_json, JSONParseError


class OpenCodeError(Exception):
    """Raised when OpenCode CLI returns an error or produces unparseable output."""


class OpenCodeTimeout(OpenCodeError):
    """Raised when OpenCode CLI exceeds timeout."""


class OpenCodeCLI:
    """Wraps OpenCode CLI (``opencode run`` + ``opencode export``).

    Strategy: always extract the final result via ``opencode export <sid>``
    which reads the complete session transcript from opencode's internal
    store.  The stream stdout from ``opencode run --format json`` is used
    only for real-time logging and session ID extraction — we never try to
    parse a result from it.
    """

    _HEARTBEAT_SECONDS: float = 30.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_command(
        session_id: str | None,
        is_resume: bool,
        model: str | None = None,
        cwd: str | None = None,
    ) -> tuple[list[str], str | None]:
        """Build the opencode CLI command.

        Returns (cmd, effective_session_id) — *effective_session_id* is the
        session ID that opencode will actually use.  On first run we do NOT
        pass ``--session`` so opencode creates a fresh session whose ID we
        extract from the ``step_start`` event.
        """
        opencode_exe = shutil.which("opencode") or "opencode"
        cmd = [opencode_exe, "run", "--format", "json"]
        effective_sid: str | None = None
        if is_resume and session_id:
            cmd.extend(["--session", session_id])
            effective_sid = session_id
        if model and "/" in model:
            cmd.extend(["--model", model])
        if cwd:
            cmd.extend(["--dir", cwd])
        return cmd, effective_sid

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | None:
        """Try to parse *text* as a JSON dict.  Returns ``None`` on failure."""
        if not text.strip():
            return None
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        try:
            return extract_json(text)
        except JSONParseError:
            return None

    # ------------------------------------------------------------------
    # Streaming stdout consumer — log only, no result extraction
    # ------------------------------------------------------------------

    async def _consume_stream(
        self,
        proc: asyncio.subprocess.Process,
        node_id: str,
        logger: logging.Logger | None,
    ) -> str | None:
        """Consume stdout from ``opencode run --format json``.

        Logs text / step events for real-time feedback and extracts the
        session ID.  Returns the session ID (or ``None`` if not found).
        Does NOT attempt to parse a result — we always use export for that.
        """
        text_parts: list[str] = []
        event_count = 0
        last_heartbeat = time.monotonic()
        session_id: str | None = None

        stdout_bytes = await proc.stdout.read()
        lines = stdout_bytes.decode("utf-8", errors="replace").split("\n")

        for line_str in lines:
            line_str = line_str.strip()
            if not line_str:
                continue

            event_count += 1

            try:
                event = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            evt_type = event.get("type", "")

            if evt_type == "step_start":
                session_id = event.get("sessionID") or session_id
                if logger:
                    logger.info(f"[{node_id}] 🔧 Step start (session={(session_id or '?')[:16]})")
                last_heartbeat = time.monotonic()

            elif evt_type == "text":
                text = event.get("part", {}).get("text", "").strip()
                if text:
                    text_parts.append(text)
                    if logger:
                        logger.info(f"[{node_id}] 💬 {text[:300]}{'...' if len(text) > 300 else ''}")
                    last_heartbeat = time.monotonic()

            elif evt_type == "step_finish":
                if logger:
                    tokens = event.get("part", {}).get("tokens", {})
                    logger.info(f"[{node_id}] 🏁 Step finish (in={tokens.get('input','?')}, out={tokens.get('output','?')})")
                last_heartbeat = time.monotonic()

            elif evt_type == "error":
                err_data = event.get("error", {})
                err_msg = err_data.get("data", {}).get("message", str(err_data))
                if logger:
                    logger.error(f"[{node_id}] ❌ {err_msg[:300]}")
                raise OpenCodeError(err_msg)

            now = time.monotonic()
            if logger and now - last_heartbeat > self._HEARTBEAT_SECONDS:
                logger.info(f"[{node_id}] ⏳ working… ({event_count} events)")
                last_heartbeat = now

        if logger:
            logger.info(f"[{node_id}] Stream ended: {event_count} events, {len(text_parts)} text parts")

        return session_id

    # ------------------------------------------------------------------
    # Export — the ONLY source of truth for the result
    # ------------------------------------------------------------------

    async def _export_and_extract(
        self,
        session_id: str,
        node_id: str,
        logger: logging.Logger | None,
    ) -> dict[str, Any]:
        """Run ``opencode export <session_id>`` and extract the assistant's text.

        The export JSON contains the full transcript with all messages.
        We extract text from the LAST assistant message (the final output).
        """
        opencode_exe = shutil.which("opencode") or "opencode"
        cmd = [opencode_exe, "export", session_id]

        if logger:
            logger.info(f"[{node_id}] 📤 Exporting session {session_id[:20]}…")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=30.0,
        )

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            raise OpenCodeError(
                f"opencode export failed (code {proc.returncode}): {stderr_text[:300]}"
            )

        try:
            export_data = json.loads(stdout_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise OpenCodeError(f"Failed to parse opencode export JSON: {e}")

        # Collect text from the LAST assistant message
        messages = export_data.get("messages", [])
        text_parts: list[str] = []
        for msg in reversed(messages):
            if msg.get("info", {}).get("role") != "assistant":
                continue
            for part in msg.get("parts", []):
                if part.get("type") == "text" and part.get("text", "").strip():
                    text_parts.append(part["text"].strip())
            if text_parts:
                break

        text_parts.reverse()
        combined = "\n".join(text_parts)

        if logger:
            logger.info(f"[{node_id}] 📤 Export: {len(text_parts)} text parts, {len(combined)} chars")

        if not combined.strip():
            raise OpenCodeError("No text found in opencode export — model produced no output")

        # Extract JSON from the text
        try:
            parsed_result = json.loads(combined)
        except json.JSONDecodeError:
            try:
                parsed_result = extract_json(combined)
            except JSONParseError as e:
                raise OpenCodeError(
                    f"No JSON found in export text ({len(combined)} chars): {combined[:300]}"
                )

        parsed_result["_session_id"] = session_id
        return parsed_result

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
        model: str | None = None,
        timeout_seconds: int = 1800,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        """Run opencode and extract the result via export.

        1. Start ``opencode run --format json``, pipe prompt to stdin
        2. Consume stdout for logging + session ID extraction
        3. Wait for process to exit
        4. Run ``opencode export <session_id>`` to get the full transcript
        5. Parse JSON from the assistant's final text
        """
        merged_env = {**os.environ, **(env or {})}

        cmd, effective_sid = self._build_command(
            session_id=session_id, is_resume=is_resume, model=model, cwd=cwd,
        )
        cmd.append("-")

        if logger:
            logger.info(f"[{node_id}] CMD: {' '.join(cmd[:6])}...  prompt={len(prompt)}chars")

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
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()

            extracted_sid = await asyncio.wait_for(
                self._consume_stream(proc, node_id, logger),
                timeout=timeout_seconds,
            )

            stderr_bytes = await proc.stderr.read()
            await proc.wait()

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise OpenCodeTimeout(
                f"OpenCode timed out after {timeout_seconds}s for node '{node_id}'"
            )
        except OpenCodeError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise

        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        if logger and stderr:
            logger.info(f"[{node_id}] STDERR: {stderr[:300]}")

        if proc.returncode != 0 and proc.returncode is not None:
            raise OpenCodeError(
                f"OpenCode exited with code {proc.returncode} for node '{node_id}'. "
                f"stderr: {stderr[:500]}"
            )

        # Resolve session ID for export
        export_sid = extracted_sid or effective_sid or session_id
        if not export_sid:
            raise OpenCodeError(
                f"No session ID found for node '{node_id}' — "
                f"cannot run opencode export"
            )

        return await self._export_and_extract(export_sid, node_id, logger)
