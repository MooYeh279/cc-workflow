"""Template variable resolver for {{ var.path }} syntax."""

from __future__ import annotations

import re
from typing import Any

TemplateContext = dict[str, Any]

_VAR_PATTERN = re.compile(r"\{\{\s*([^}]+)\s*\}\}")


def _resolve_path(path: str, context: TemplateContext) -> Any:
    """Resolve a dotted path like 'nodes.coding.output.files_changed' against context."""
    parts = path.strip().split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return ""
        if current is None:
            return ""
    return current


def resolve_template(value: Any, context: TemplateContext) -> Any:
    """Resolve {{ }} template variables in a value.

    Supports:
    - Plain strings with embedded templates: "Status: {{ nodes.x.status }}"
    - Entire values that are single templates: "{{ inputs.foo }}"
    - Nested dicts: recursively resolves string values
    """
    if isinstance(value, dict):
        return {k: resolve_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_template(item, context) for item in value]
    if not isinstance(value, str):
        return value

    if "{{" not in value:
        return value

    matches = _VAR_PATTERN.findall(value)

    # If the entire string is a single template expression, return the raw value
    if _VAR_PATTERN.fullmatch(value.strip()):
        return _resolve_path(matches[0].strip(), context)

    # Otherwise, substitute each variable with its string representation
    result = value
    for var_path in matches:
        var_path = var_path.strip()
        resolved = _resolve_path(var_path, context)
        if not isinstance(resolved, str):
            resolved = str(resolved)
        result = result.replace("{{ " + var_path + " }}", resolved)
        result = result.replace("{{" + var_path + "}}", resolved)
    return result
