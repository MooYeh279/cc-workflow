"""OpenCode CLI adapter — subprocess management with streaming JSON output."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from typing import Any

from wflow.common.json_parser import extract_json, JSONParseError


class OpenCodeError(Exception):
    """Raised when OpenCode CLI returns an error or produces unparseable output."""


class OpenCodeTimeout(OpenCodeError):
    """Raised when OpenCode CLI exceeds timeout."""


class OpenCodeCLI:
    """Wraps OpenCode CLI (``opencode run``) subprocess calls with streaming logs.

    Uses ``opencode run --format json`` to get structured JSON events.
    OpenCode produces events of type: ``step_start``, ``text``, ``step_finish``,
    ``error``.  The result is extracted from ``text`` events during the stream.
    """

    _HEARTBEAT_SECONDS: float = 30.0

    def __init__(self):
        # Stores the session ID extracted from the most recent step_start
        # event so that the run() method can access it for export fallback.
        self._last_extracted_sid: str | None = None

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
        session ID that opencode will actually use.  On first run
        (``is_resume=False``) we **do not** pass ``--session`` so that
        opencode creates a fresh session whose ID we extract from the
        ``step_start`` event.  On resume we pass the provider-assigned ID.
        """
        opencode_exe = shutil.which("opencode") or "opencode"
        cmd = [opencode_exe, "run", "--format", "json"]
        effective_sid: str | None = None
        if is_resume and session_id:
            cmd.extend(["--session", session_id])
            effective_sid = session_id
        # OpenCode expects ``provider/model`` format; skip if not in that form
        if model and "/" in model:
            cmd.extend(["--model", model])
        # Pin the working directory so files land inside the workspace
        if cwd:
            cmd.extend(["--dir", cwd])
        return cmd, effective_sid

    # ------------------------------------------------------------------
    # Export-based result extraction (fallback when stdout has no text)
    # ------------------------------------------------------------------

    async def _export_and_extract(
        self,
        session_id: str,
        node_id: str,
        logger: logging.Logger | None,
    ) -> dict[str, Any]:
        """Run ``opencode export <session_id>`` and extract the assistant's text.

        Used as a fallback when the streaming stdout contained no ``text``
        events (e.g. when the model only used tools without a follow-up
        text response).  The export JSON contains the full transcript with
        all text parts.
        """
        opencode_exe = shutil.which("opencode") or "opencode"
        cmd = [opencode_exe, "export", session_id]

        if logger:
            logger.info(f"[{node_id}] Running opencode export as fallback…")

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
                f"opencode export failed with code {proc.returncode}: "
                f"{stderr_text[:300]}"
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

        # Reverse back to chronological order
        text_parts.reverse()
        combined = "\n".join(text_parts)

        if logger:
            logger.info(
                f"[{node_id}] Export fallback: {len(text_parts)} text parts, "
                f"{len(combined)} chars combined"
            )

        if not combined.strip():
            raise OpenCodeError("No text found in opencode export")

        # Try to extract JSON
        try:
            parsed_result = json.loads(combined)
        except json.JSONDecodeError:
            try:
                parsed_result = extract_json(combined)
            except JSONParseError as e:
                raise OpenCodeError(
                    f"No JSON found in export text ({len(combined)} chars): "
                    f"{combined[:300]}"
                )

        parsed_result["_session_id"] = session_id
        return parsed_result

    # ------------------------------------------------------------------
    # Streaming parser — extracts result DURING streaming (one pass)
    # ------------------------------------------------------------------

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

    async def _stream_and_parse(
        self,
        proc: asyncio.subprocess.Process,
        node_id: str,
        logger: logging.Logger | None,
    ) -> dict[str, Any]:
        """Consume JSON-line stdout, log status, return parsed result dict.

        OpenCode emits ``step_start`` → ``text`` (1+) → ``step_finish``
        plus ``error`` events.  The model may produce text in **multiple**
        steps (e.g. step 1 is tool-use / analysis, step 2 is the final
        output).  We collect every ``text`` event and attempt JSON extraction
        — first by trying each text part individually (newest-first, since
        the final step usually contains the result), then by joining all
        parts and falling back to ``extract_json``.
        """
        text_parts: list[str] = []
        event_count = 0
        last_heartbeat = time.monotonic()
        extracted_session_id: str | None = None

        # ── read ALL stdout lines first, then process ──────────────────────────
        # Using .read() + split is more robust than a while-readline loop
        # because readline() can return the entire buffer as a single "line"
        # when newlines are missing or when the subprocess output is buffered
        # differently than expected.
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
                if logger:
                    logger.debug(
                        f"[{node_id}] Skipping non-JSON line "
                        f"({len(line_str)} chars)"
                    )
                continue

            evt_type = event.get("type", "")

            # --- step_start --------------------------------------------------
            if evt_type == "step_start":
                extracted_session_id = event.get("sessionID") or extracted_session_id
                self._last_extracted_sid = extracted_session_id
                if logger:
                    sid_short = (extracted_session_id or "?")[:16]
                    logger.info(f"[{node_id}] 🔧 Step start (session={sid_short})")
                last_heartbeat = time.monotonic()

            # --- text — collect content from the agent -----------------------
            elif evt_type == "text":
                part = event.get("part", {})
                text = part.get("text", "").strip()
                if text:
                    text_parts.append(text)
                    if logger:
                        logger.info(
                            f"[{node_id}] 💬 {text[:300]}{'...' if len(text) > 300 else ''}"
                        )
                    last_heartbeat = time.monotonic()

            # --- step_finish -------------------------------------------------
            elif evt_type == "step_finish":
                if logger:
                    tokens = event.get("part", {}).get("tokens", {})
                    logger.info(
                        f"[{node_id}] 🏁 Step finish "
                        f"(in={tokens.get('input', '?')}, out={tokens.get('output', '?')})"
                    )
                last_heartbeat = time.monotonic()

            # --- error -------------------------------------------------------
            elif evt_type == "error":
                err_data = event.get("error", {})
                err_msg = err_data.get("data", {}).get("message", str(err_data))
                if logger:
                    logger.error(f"[{node_id}] ❌ {err_msg[:300]}")
                raise OpenCodeError(err_msg)

            # --- periodic heartbeat ------------------------------------------
            now = time.monotonic()
            if logger and now - last_heartbeat > self._HEARTBEAT_SECONDS:
                logger.info(
                    f"[{node_id}] ⏳ working… ({event_count} events)"
                )
                last_heartbeat = now

        if logger:
            logger.info(
                f"[{node_id}] Stream ended: {event_count} events, "
                f"{len(text_parts)} text parts"
            )

        # --- Extract result ------------------------------------------------------
        # Strategy 1: try each text part individually, newest-first.
        # The final step usually contains the structured output we want.
        # Parsing parts one-by-one avoids the problem where joining multiple
        # steps' JSON outputs creates invalid compound JSON.
        for part_text in reversed(text_parts):
            parsed = self._try_parse_json(part_text)
            if parsed is not None:
                parsed["_session_id"] = extracted_session_id
                return parsed

        # Strategy 2: join all parts and try the combined text via extract_json
        # (handles multi-line JSON that was split across events).
        combined = "\n".join(text_parts)
        if combined.strip():
            parsed = self._try_parse_json(combined)
            if parsed is not None:
                parsed["_session_id"] = extracted_session_id
                return parsed

        # Strategy 3: look for JSON in the raw stdout (last resort)
        raw_text = stdout_bytes.decode("utf-8", errors="replace")
        parsed = self._try_parse_json(raw_text)
        if parsed is not None:
            parsed["_session_id"] = extracted_session_id
            return parsed

        if logger:
            logger.error(
                f"[{node_id}] No result. text_parts={len(text_parts)} "
                f"combined_len={len(combined)} preview={combined[:300]}"
            )
        raise OpenCodeError("No result found in stream output")

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
            raise OpenCodeTimeout(
                f"OpenCode timed out after {timeout_seconds}s for node '{node_id}'"
            )
        except OpenCodeError as stream_error:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

            # Determine the effective session ID for export fallback.
            # Priority: (1) provider SID extracted from step_start event,
            #           (2) session_id passed to run() (resume case).
            export_sid = self._last_extracted_sid or effective_sid

            # Attempt export fallback when stdout had no text events
            if export_sid and "No result found in stream output" in str(stream_error):
                if logger:
                    logger.info(
                        f"[{node_id}] Streaming found no text — "
                        f"falling back to opencode export ({export_sid[:20]})"
                    )
                try:
                    parsed = await self._export_and_extract(
                        export_sid, node_id, logger,
                    )
                    return parsed
                except OpenCodeError:
                    pass  # Re-raise the original stream error below
            raise

        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        if logger and stderr:
            logger.info(f"[{node_id}] STDERR: {stderr[:300]}")

        if proc.returncode != 0 and proc.returncode is not None:
            raise OpenCodeError(
                f"OpenCode exited with code {proc.returncode} for node '{node_id}'. "
                f"stderr: {stderr[:500]}"
            )

        # Ensure session_id is set (use extracted one from events if available)
        if "_session_id" not in parsed:
            parsed["_session_id"] = session_id or effective_sid
        return parsed
