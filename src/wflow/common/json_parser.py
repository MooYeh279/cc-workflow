"""Shared JSON extraction utilities — used by CLI adapters to parse structured
output from stream-json or text responses."""

from __future__ import annotations

import json
import re
from typing import Any


class JSONParseError(Exception):
    """No valid JSON found in the provided text."""


def extract_json(text: str) -> dict[str, Any]:
    """Try multiple strategies to extract a JSON dict from arbitrary text.

    Strategies (in order):
    1. Direct JSON parse of the whole text.
    2. Extract from ```json ... ``` fenced code blocks.
    3. Find balanced braces and parse.

    Raises :exc:`JSONParseError` if no valid JSON dict is found.
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Strategy 2: ```json ... ``` blocks (handle any whitespace around fences)
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 3: balanced braces
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            parsed = json.loads(text[brace_start:brace_end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise JSONParseError(f"No valid JSON found: {text[:500]}")
