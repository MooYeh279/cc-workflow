import pytest
from unittest.mock import AsyncMock, MagicMock
from wflow.services.workflow_svc import WorkflowService
from wflow.services.execution_svc import ExecutionService
from wflow.services.schedule_svc import ScheduleService


@pytest.fixture
def db_session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_workflow_service_create(db_session):
    svc = WorkflowService(db_session)
    wf = await svc.create(
        name="test-wf",
        config={"nodes": [], "edges": []},
    )
    assert wf.name == "test-wf"
    db_session.add.assert_called_once()
    db_session.commit.assert_called()


@pytest.mark.asyncio
async def test_workflow_service_list(db_session):
    from wflow.models.db import Workflow
    wf1 = Workflow(id="w1", name="a", config="{}")
    wf2 = Workflow(id="w2", name="b", config="{}")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [wf1, wf2]
    db_session.execute = AsyncMock(return_value=result)

    svc = WorkflowService(db_session)
    workflows = await svc.list_all()

    assert len(workflows) == 2


@pytest.mark.asyncio
async def test_execution_service_start_run(db_session):
    svc = ExecutionService(db_session)
    run = await svc.start_run("w1", {"task": "hello"})

    assert run.workflow_id == "w1"
    assert run.status == "pending"
    db_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_execution_service_pause_run(db_session):
    from wflow.models.db import WorkflowRun
    run = WorkflowRun(id="r1", workflow_id="w1", status="running", context="{}")
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    db_session.execute = AsyncMock(return_value=result)

    svc = ExecutionService(db_session)
    updated = await svc.pause_run("r1")

    assert updated.status == "paused"


@pytest.mark.asyncio
async def test_schedule_service_create(db_session):
    svc = ScheduleService(db_session)
    job = await svc.create("wf-1", "0 9 * * *")
    assert job.workflow_id == "wf-1"
    assert job.cron_expr == "0 9 * * *"
