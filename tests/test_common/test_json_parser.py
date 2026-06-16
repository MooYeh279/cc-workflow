import json
import pytest
from wflow.common.json_parser import extract_json, JSONParseError, _try_parse

BS = chr(92)  # single backslash character

# NOTE: \t (tab), \r (CR), \n (LF), \b (backspace), \f (form-feed) are
# VALID JSON escape sequences.  json_repair intentionally leaves them
# unchanged because it cannot distinguish a deliberate escape from an
# unescaped Windows path segment like \test or \report.  Test paths
# below avoid those letters to focus on the common case: \A, \D, \w, etc.


# ---------------------------------------------------------------------------
# _try_parse (uses json_repair.repair_json under the hood)
# ---------------------------------------------------------------------------

def test_try_parse_valid():
    """Valid JSON is returned unchanged."""
    result = _try_parse('{"ok": true}')
    assert result == {"ok": True}


def test_try_parse_windows_paths():
    """Windows paths with invalid escapes are repaired."""
    raw = (
        '{"report_path": "D:'
        + BS + 'AI' + BS + 'DeepResearchAI' + BS + 'workspace' + BS + 'output.md",'
        + ' "summary": "test"}'
    )
    result = _try_parse(raw)
    expected_path = (
        "D:" + BS + "AI" + BS + "DeepResearchAI" + BS + "workspace" + BS + "output.md"
    )
    assert result["report_path"] == expected_path
    assert result["summary"] == "test"


def test_try_parse_preserves_valid_escapes():
    """Valid JSON escapes (\\n, \\t, \\\\, \\") are not corrupted."""
    valid = '{"key": "line1\\nline2\\ttab", "path": "C:\\\\Users\\\\test", "quote": "say \\"hi\\""}'
    result = _try_parse(valid)
    assert result["key"] == "line1\nline2\ttab"
    assert result["path"] == "C:\\Users\\test"
    assert result["quote"] == 'say "hi"'


def test_try_parse_raises_on_unrepairable():
    """Totally broken text raises JSONDecodeError."""
    with pytest.raises(json.JSONDecodeError):
        _try_parse("not json at all")


# ---------------------------------------------------------------------------
# extract_json — strategy 1 (direct parse)
# ---------------------------------------------------------------------------

def test_extract_direct_valid():
    assert extract_json('{"ok": true}') == {"ok": True}


def test_extract_direct_with_windows_paths():
    raw = (
        '{"report_path": "D:'
        + BS + 'AI' + BS + 'DeepResearchAI' + BS + 'workspace' + BS + 'output.md"}'
    )
    result = extract_json(raw)
    expected = "D:" + BS + "AI" + BS + "DeepResearchAI" + BS + "workspace" + BS + "output.md"
    assert result["report_path"] == expected


# ---------------------------------------------------------------------------
# extract_json — strategy 2 (code fence)
# ---------------------------------------------------------------------------

def test_extract_code_fence_normal():
    text = '```json\n{"ok": true}\n```'
    assert extract_json(text) == {"ok": True}


def test_extract_code_fence_inline():
    """Code fence without newline after opening fence (inline)."""
    text = '```json{"ok": true}\n```'
    assert extract_json(text) == {"ok": True}


def test_extract_code_fence_no_lang_tag():
    text = '```\n{"ok": true}\n```'
    assert extract_json(text) == {"ok": True}


def test_extract_code_fence_windows_paths():
    raw = (
        '```json\n'
        '{"report_path": "D:'
        + BS + 'AI' + BS + 'DeepResearchAI' + BS + 'workspace' + BS + 'output.md"}\n'
        '```'
    )
    result = extract_json(raw)
    expected_path = (
        "D:" + BS + "AI" + BS + "DeepResearchAI" + BS + "workspace" + BS + "output.md"
    )
    assert result["report_path"] == expected_path


def test_extract_code_fence_surrounding_text():
    text = (
        'All three reports exist.\n'
        '```json\n'
        '{"report_path": "D:'
        + BS + 'AI' + BS + 'summary.md", "summary": "done"}\n'
        '```'
    )
    result = extract_json(text)
    assert result["summary"] == "done"
    assert BS + "AI" + BS + "summary.md" in result["report_path"]


# ---------------------------------------------------------------------------
# extract_json — strategy 3 (balanced braces)
# ---------------------------------------------------------------------------

def test_extract_balanced_braces():
    text = 'some text {"key": "value"} more text'
    assert extract_json(text) == {"key": "value"}


def test_extract_balanced_braces_windows_paths():
    raw = (
        'prefix\n'
        '{"report_path": "D:'
        + BS + 'AI' + BS + 'DeepResearchAI' + BS + 'workspace' + BS + 'output.md"}\n'
        'suffix'
    )
    result = extract_json(raw)
    expected_path = (
        "D:" + BS + "AI" + BS + "DeepResearchAI" + BS + "workspace" + BS + "output.md"
    )
    assert result["report_path"] == expected_path


def test_extract_balanced_braces_nested():
    text = 'outer {"inner": {"deep": true}} tail'
    assert extract_json(text) == {"inner": {"deep": True}}


# ---------------------------------------------------------------------------
# extract_json — error cases
# ---------------------------------------------------------------------------

def test_extract_no_json_raises():
    with pytest.raises(JSONParseError, match="No valid JSON found"):
        extract_json("just some text with no json at all")


# ---------------------------------------------------------------------------
# extract_json — regression: valid JSON should not be corrupted
# ---------------------------------------------------------------------------

def test_extract_valid_json_unchanged():
    """Valid JSON with proper escapes is parsed correctly."""
    text = '```json\n{"key": "line1\\nline2", "path": "C:\\\\Users\\\\test"}\n```'
    result = extract_json(text)
    assert result["key"] == "line1\nline2"
    assert result["path"] == "C:\\Users\\test"
