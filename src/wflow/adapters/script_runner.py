"""Script node runner — executes external commands via subprocess with stdin/stdout."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from typing import Any


class ScriptError(Exception):
    """Raised when a script node fails."""


class ScriptRunner:
    """Executes external scripts/commands as subprocess.

    Context (inputs + upstream output) is serialized as JSON and piped
    to stdin. The script MUST write a JSON object to stdout as its result.
    """

    async def run(
        self,
        command: str,
        context: dict[str, Any],
        timeout_seconds: int = 300,
        cwd: str | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        """Execute a command, pass context as stdin JSON, parse stdout JSON.

        Args:
            command: The command to execute (e.g. "python ./scripts/validate.py")
            context: Dict with "inputs" and "upstream" keys to pass via stdin
            timeout_seconds: Max execution time
            cwd: Working directory for the subprocess
            logger: Optional logger

        Returns:
            Parsed JSON output from stdout

        Raises:
            ScriptError: On non-zero exit, timeout, or invalid JSON output
        """
        stdin_data = json.dumps(context, ensure_ascii=False)

        if logger:
            logger.info(f"[script] CMD: {command}  cwd={cwd}")
            logger.info(f"[script] INPUT ({len(stdin_data)} chars): {stdin_data[:200]}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(command),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode("utf-8")),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise ScriptError(f"Script timed out after {timeout_seconds}s: {command}")
        except FileNotFoundError:
            raise ScriptError(f"Command not found: {shlex.split(command)[0]}")
        except Exception as e:
            raise ScriptError(f"Script execution failed: {e}")

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if logger:
            logger.info(f"[script] STDOUT ({len(stdout)} chars): {stdout[:300]}")
            if stderr:
                logger.info(f"[script] STDERR: {stderr[:300]}")

        if proc.returncode != 0:
            raise ScriptError(
                f"Script exited with code {proc.returncode}: {command}\n"
                f"stderr: {stderr[:500]}"
            )

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            raise ScriptError(
                f"Script stdout is not valid JSON: {stdout[:500]}"
            )

        if not isinstance(result, dict):
            raise ScriptError(
                f"Script must output a JSON object, got {type(result).__name__}"
            )

        return result
