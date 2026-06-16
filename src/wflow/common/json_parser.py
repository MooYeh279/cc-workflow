"""Shared JSON extraction utilities — used by CLI adapters to parse structured
output from stream-json or text responses."""

from __future__ import annotations

import json
import re
from typing import Any

from json_repair import repair_json


class JSONParseError(Exception):
    """No valid JSON found in the provided text."""


def _try_parse(text: str) -> Any:
    """Parse JSON, falling back to :func:`json_repair.repair_json` on failure.

    Fast path: :func:`json.loads` for valid JSON.
    Fallback: :func:`repair_json` fixes common AI-generation issues
    (unescaped Windows path backslashes, trailing commas, missing
    quotes, etc.) before a second :func:`json.loads` attempt.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    repaired = repair_json(text)
    if not repaired:
        raise json.JSONDecodeError("empty after repair", text, 0)
    return json.loads(repaired)


def extract_json(text: str) -> dict[str, Any]:
    """Try multiple strategies to extract a JSON dict from arbitrary text.

    Strategies (in order):
    1. Direct JSON parse of the whole text.
    2. Extract from ```json ... ``` fenced code blocks.
    3. Find balanced braces and parse.

    All strategies use :func:`json_repair.repair_json` to tolerate
    invalid JSON escape sequences commonly produced by AI models
    (e.g. Windows file paths with unescaped backslashes).

    Raises :exc:`JSONParseError` if no valid JSON dict is found.
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        parsed = _try_parse(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Strategy 2: ```json ... ``` blocks (handle any whitespace around fences;
    # newline after opening fence is optional — Claude may output inline fences)
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            parsed = _try_parse(m.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 3: balanced braces
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            parsed = _try_parse(text[brace_start:brace_end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise JSONParseError(f"No valid JSON found: {text[:500]}")
