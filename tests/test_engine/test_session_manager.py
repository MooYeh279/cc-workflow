import pytest
from unittest.mock import AsyncMock, MagicMock
from wflow.engine.session_manager import SessionManager


@pytest.fixture
def db_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def session_mgr(db_session):
    return SessionManager(db_session)


@pytest.mark.asyncio
async def test_get_or_create_session_creates_new(session_mgr, db_session):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db_session.execute.return_value = result

    session_id = await session_mgr.get_or_create("run-1", "coding")

    assert session_id is not None
    assert len(session_id) > 0
    db_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_session_returns_existing(session_mgr, db_session):
    from wflow.models.db import Session as SessionModel
    existing = SessionModel(id="sess-existing", run_id="run-1", node_id="coding", status="active")

    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db_session.execute.return_value = result

    session_id = await session_mgr.get_or_create("run-1", "coding")

    assert session_id == "sess-existing"


@pytest.mark.asyncio
async def test_is_session_valid_when_execution_completed(session_mgr, db_session):
    from wflow.models.db import NodeExecution
    completed = NodeExecution(id="ne-1", run_id="run-1", node_id="coding",
                               type="agent", status="completed")
    result = MagicMock()
    result.scalar_one_or_none.return_value = completed
    db_session.execute.return_value = result

    assert await session_mgr.is_session_valid("run-1", "coding") is True


@pytest.mark.asyncio
async def test_is_session_valid_when_no_completed_execution(session_mgr, db_session):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db_session.execute.return_value = result

    assert await session_mgr.is_session_valid("run-1", "new-node") is False


@pytest.mark.asyncio
async def test_cleanup_expired_sessions(session_mgr, db_session):
    result = MagicMock()
    result.rowcount = 3
    db_session.execute = AsyncMock(return_value=result)

    deleted = await session_mgr.cleanup_expired(retention_days=30)
    assert deleted == 3
    assert db_session.execute.call_count >= 1
