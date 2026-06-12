import pytest
from unittest.mock import AsyncMock, MagicMock
from wflow.engine.scheduler import CronJobManager


@pytest.fixture
def db_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.delete = MagicMock()
    return session


@pytest.mark.asyncio
async def test_cron_job_manager_create_job(db_session):
    mgr = CronJobManager(db_session)

    job = await mgr.create(workflow_id="wf-1", cron_expr="0 9 * * *", inputs={"task": "daily"})

    assert job.workflow_id == "wf-1"
    assert job.cron_expr == "0 9 * * *"
    db_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_cron_job_manager_list_jobs(db_session):
    from wflow.models.db import CronJob
    job = CronJob(id="cj-1", workflow_id="wf-1", cron_expr="0 9 * * *", enabled=1)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [job]
    db_session.execute = AsyncMock(return_value=result)

    mgr = CronJobManager(db_session)
    jobs = await mgr.list_all()

    assert len(jobs) == 1
    assert jobs[0].cron_expr == "0 9 * * *"


@pytest.mark.asyncio
async def test_cron_job_manager_toggle(db_session):
    from wflow.models.db import CronJob
    job = CronJob(id="cj-1", workflow_id="wf-1", cron_expr="0 * * * *", enabled=1)

    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db_session.execute = AsyncMock(return_value=result)

    mgr = CronJobManager(db_session)
    updated = await mgr.toggle("cj-1")

    assert updated is not None
    assert updated.enabled == 0


@pytest.mark.asyncio
async def test_cron_job_manager_delete(db_session):
    result = MagicMock()
    result.rowcount = 1
    db_session.execute = AsyncMock(return_value=result)

    mgr = CronJobManager(db_session)
    deleted = await mgr.delete("cj-1")

    assert deleted is True
    db_session.execute.assert_called_once()
    db_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_cron_job_manager_delete_not_found(db_session):
    result = MagicMock()
    result.rowcount = 0
    db_session.execute = AsyncMock(return_value=result)

    mgr = CronJobManager(db_session)
    deleted = await mgr.delete("nonexistent")

    assert deleted is False


@pytest.mark.asyncio
async def test_cron_job_manager_get_not_found(db_session):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db_session.execute = AsyncMock(return_value=result)

    mgr = CronJobManager(db_session)
    job = await mgr.get("nonexistent")

    assert job is None
