import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from wflow.models.db import Base, Workflow, WorkflowRun, NodeExecution, Session, CronJob, RunLog


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///file:test-db?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.mark.asyncio
async def test_create_workflow(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", description="a test", config='{"nodes":[]}')
    db_session.add(wf)
    await db_session.commit()

    result = await db_session.get(Workflow, wid)
    assert result is not None
    assert result.name == "test-wf"
    assert result.status == "active"


@pytest.mark.asyncio
async def test_create_workflow_run(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", config='{"nodes":[]}')
    db_session.add(wf)
    await db_session.commit()

    rid = str(uuid.uuid4())
    run = WorkflowRun(id=rid, workflow_id=wid, status="pending", context="{}")
    db_session.add(run)
    await db_session.commit()

    result = await db_session.get(WorkflowRun, rid)
    assert result is not None
    assert result.workflow_id == wid
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_node_execution_lifecycle(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", config='{"nodes":[]}')
    rid = str(uuid.uuid4())
    run = WorkflowRun(id=rid, workflow_id=wid, status="running", context="{}")
    db_session.add_all([wf, run])
    await db_session.commit()

    nid = str(uuid.uuid4())
    ne = NodeExecution(id=nid, run_id=rid, node_id="coding", type="agent",
                       session_id="sess-1", status="completed", retry_count=0,
                       input='{"task":"test"}', output='{"ok":true}')
    db_session.add(ne)
    await db_session.commit()

    result = await db_session.get(NodeExecution, nid)
    assert result.output == '{"ok":true}'


@pytest.mark.asyncio
async def test_run_log_insert(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", config='{"nodes":[]}')
    rid = str(uuid.uuid4())
    run = WorkflowRun(id=rid, workflow_id=wid, status="running", context="{}")
    db_session.add_all([wf, run])
    await db_session.commit()

    log = RunLog(run_id=rid, node_id="coding", level="info", message="started")
    db_session.add(log)
    await db_session.commit()

    assert log.id is not None
