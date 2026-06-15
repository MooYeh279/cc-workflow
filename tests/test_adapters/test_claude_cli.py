import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from wflow.adapters.claude_cli import ClaudeCLI, ClaudeCLIError, ClaudeCLITimeout


@pytest.fixture
def claude():
    return ClaudeCLI()


def test_build_command_stream_json(claude):
    cmd = claude._build_command(
        session_id="sess-001",
        is_resume=False,
        tools_allowed=["Read", "Write"],
    )
    assert "claude" in cmd[0].lower()
    assert "--print" in cmd
    assert "--verbose" in cmd
    assert "stream-json" in cmd
    assert "--session-id" in cmd
    assert "sess-001" in cmd
    assert "--allowedTools" in cmd
    assert "Read,Write" in cmd


def test_build_command_resume(claude):
    cmd = claude._build_command(
        session_id="sess-001",
        is_resume=True,
        tools_allowed=["Edit", "Bash"],
    )
    assert "--resume" in cmd
    assert "sess-001" in cmd


def test_build_input_format(claude):
    result = claude._build_input("Write code")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "user"
    assert parsed["message"]["role"] == "user"
    content = parsed["message"]["content"]
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "Write code"


# ---------------------------------------------------------------------------
# Helpers to build mock stdout that uses read() (chunked) instead of
# readline().  The real implementation reads up to 65536 bytes at a time;
# the tests feed everything as one chunk + empty chunk (EOF).
# ---------------------------------------------------------------------------

def _mock_stdout_read(*lines: str) -> MagicMock:
    """Return a MagicMock whose ``.read()`` is an AsyncMock for chunked reads.

    Each *line* is joined with newlines, encoded, and delivered as a single
    read chunk.  A trailing empty bytes chunk signals EOF.
    """
    payload = "\n".join(lines) + "\n"
    mock_stdout = MagicMock()
    mock_stdout.read = AsyncMock(side_effect=[payload.encode("utf-8"), b""])
    return mock_stdout


def _mock_stdout_read_raw(*chunks: bytes) -> MagicMock:
    """Return a MagicMock whose ``.read()`` delivers explicit byte chunks."""
    mock_stdout = MagicMock()
    mock_stdout.read = AsyncMock(side_effect=list(chunks))
    return mock_stdout


# ---------------------------------------------------------------------------
# _stream_and_parse tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_and_parse_result_event(claude):
    """Result event is parsed inline during streaming."""
    mock_proc = MagicMock()
    mock_proc.stdout = _mock_stdout_read(
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet", "tools": ["Read"]}),
        json.dumps({"type": "result", "result": json.dumps({"status": "done"})}),
    )

    result = await claude._stream_and_parse(mock_proc, "test", logger=None)
    assert result == {"status": "done"}


@pytest.mark.asyncio
async def test_stream_and_parse_result_dict_directly(claude):
    """Result event with a dict payload (not string-wrapped)."""
    mock_proc = MagicMock()
    mock_proc.stdout = _mock_stdout_read(
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "result", "result": {"status": "direct"}}),
    )

    result = await claude._stream_and_parse(mock_proc, "test", logger=None)
    assert result == {"status": "direct"}


@pytest.mark.asyncio
async def test_stream_and_parse_fallback_text(claude):
    """Fallback: extract JSON from assistant text when no result event."""
    mock_proc = MagicMock()
    mock_proc.stdout = _mock_stdout_read(
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": '```json\n{"summary": "complete"}\n```'}
        ]}}),
    )

    result = await claude._stream_and_parse(mock_proc, "test", logger=None)
    assert result == {"summary": "complete"}


@pytest.mark.asyncio
async def test_stream_and_parse_no_json_raises(claude):
    """Stream with no valid JSON raises error."""
    mock_proc = MagicMock()
    mock_proc.stdout = _mock_stdout_read(
        json.dumps({"type": "system", "subtype": "init"}),
    )

    with pytest.raises(ClaudeCLIError, match="No result found"):
        await claude._stream_and_parse(mock_proc, "test", logger=None)


@pytest.mark.asyncio
async def test_stream_and_parse_multi_chunk(claude):
    """Lines split across multiple read() chunks are handled correctly."""
    line1 = json.dumps({"type": "system", "subtype": "init", "model": "s", "tools": []})
    line2 = json.dumps({"type": "result", "result": json.dumps({"ok": True})})
    combined = f"{line1}\n{line2}\n"
    # Split across two chunks: middle of line1, rest of line1 + line2
    split = len(combined) // 3
    chunk1 = combined[:split].encode("utf-8")
    chunk2 = combined[split:].encode("utf-8")

    mock_proc = MagicMock()
    mock_proc.stdout = _mock_stdout_read_raw(chunk1, chunk2, b"")

    result = await claude._stream_and_parse(mock_proc, "test", logger=None)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_stream_and_parse_jumbo_line_does_not_overflow(claude):
    """A line larger than 64 KB does NOT trigger LimitOverrunError."""
    big_value = "x" * 128_000  # 128 KB — well above the old 64 KB limit
    jumbo_line = json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": big_value}]},
    })
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "s", "tools": []}),
        jumbo_line,
        json.dumps({"type": "result", "result": json.dumps({"status": "big_ok"})}),
    ])

    mock_proc = MagicMock()
    mock_proc.stdout = _mock_stdout_read_raw(stream.encode("utf-8"), b"")

    result = await claude._stream_and_parse(mock_proc, "test", logger=None)
    assert result == {"status": "big_ok"}


# ---------------------------------------------------------------------------
# run() integration tests
# ---------------------------------------------------------------------------

@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_stream_json(mock_subprocess, claude):
    stream_output = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet", "tools": ["Read"]}),
        json.dumps({"type": "result", "result": json.dumps({"ok": True})}),
    ])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    # StreamWriter.write() and .close() are regular methods, not coroutines.
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdout = _mock_stdout_read(
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet", "tools": ["Read"]}),
        json.dumps({"type": "result", "result": json.dumps({"ok": True})}),
    )
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.wait = AsyncMock()
    mock_subprocess.return_value = mock_proc

    result = await claude.run(
        prompt="Write code",
        node_id="coding",
        session_id=None,
        is_resume=False,
        tools_allowed=["Read"],
        timeout_seconds=30,
    )

    assert result["ok"] is True
    assert result["_session_id"] is not None
    stdin_call = mock_proc.stdin.write.call_args[0][0]
    assert b'"type"' in stdin_call
    assert b'"user"' in stdin_call


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_resumes_existing_session(mock_subprocess, claude):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdout = _mock_stdout_read(
        json.dumps({"type": "result", "result": json.dumps({"fixed": True})}),
    )
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.wait = AsyncMock()
    mock_subprocess.return_value = mock_proc

    result = await claude.run(
        prompt="Fix the bug",
        node_id="coding",
        session_id="existing-sess",
        is_resume=True,
        tools_allowed=["Edit"],
        timeout_seconds=30,
    )

    assert result["fixed"] is True
    assert result["_session_id"] == "existing-sess"


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_timeout(mock_subprocess, claude):
    import asyncio as aio
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.read = AsyncMock(side_effect=aio.TimeoutError())
    mock_proc.stderr = AsyncMock()
    mock_proc.kill = MagicMock()
    mock_subprocess.return_value = mock_proc

    with pytest.raises(ClaudeCLITimeout):
        await claude.run(
            prompt="long task",
            node_id="test",
            session_id=None,
            is_resume=False,
            tools_allowed=[],
            timeout_seconds=1,
        )
