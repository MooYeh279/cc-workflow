import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from wflow.adapters.script_runner import ScriptRunner, ScriptError


@pytest.fixture
def runner():
    return ScriptRunner()


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_script_success(mock_subprocess, runner):
    """Script that echoes context back as JSON."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(
        b'{"echo": {"msg": "hello"}}', b"",
    ))
    mock_subprocess.return_value = mock_proc

    result = await runner.run(
        command="python ./scripts/validate.py",
        context={"inputs": {"msg": "hello"}, "upstream": {}},
        timeout_seconds=30,
        cwd="/tmp/test",
    )

    assert result["echo"]["msg"] == "hello"


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_script_non_zero_exit(mock_subprocess, runner):
    """Non-zero exit code should raise ScriptError."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdin = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(
        b"{}", b"command not found",
    ))
    mock_subprocess.return_value = mock_proc

    with pytest.raises(ScriptError, match="exited with code 1"):
        await runner.run(
            command="invalid-command",
            context={},
            timeout_seconds=30,
        )


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_script_invalid_json(mock_subprocess, runner):
    """Non-JSON stdout should raise ScriptError."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(
        b"not json at all", b"",
    ))
    mock_subprocess.return_value = mock_proc

    with pytest.raises(ScriptError, match="stdout is not valid JSON"):
        await runner.run(
            command="python ./bad.py",
            context={},
            timeout_seconds=30,
        )


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_script_non_dict_output(mock_subprocess, runner):
    """Non-dict JSON output should raise ScriptError."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(
        b'["list", "not", "dict"]', b"",
    ))
    mock_subprocess.return_value = mock_proc

    with pytest.raises(ScriptError, match="must output a JSON object"):
        await runner.run(
            command="python ./bad.py",
            context={},
            timeout_seconds=30,
        )


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_script_timeout(mock_subprocess, runner):
    """Timeout should raise ScriptError."""
    import asyncio as aio
    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.communicate = AsyncMock(side_effect=aio.TimeoutError())
    mock_proc.kill = MagicMock()
    mock_subprocess.return_value = mock_proc

    with pytest.raises(ScriptError, match="timed out"):
        await runner.run(
            command="python ./slow.py",
            context={},
            timeout_seconds=1,
        )
