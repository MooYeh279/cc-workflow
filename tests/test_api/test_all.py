import pytest
from httpx import AsyncClient, ASGITransport
from wflow.main import create_app


@pytest.fixture
def app():
    return create_app("sqlite+aiosqlite:///file:test-api-all?mode=memory&cache=shared&uri=true")


@pytest.fixture
async def client(app):
    lifespan = app.router.lifespan_context(app)
    async with lifespan:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_status_endpoint(client):
    resp = await client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "running_workflows" in data


@pytest.mark.asyncio
async def test_create_and_list_workflows(client):
    resp = await client.post("/api/v1/workflows", json={
        "name": "test-wf", "config": {
            "nodes": [{"id": "n1", "type": "script",
                       "command": "echo test",
                       "timeout_seconds": 60,
                       "output": {"type": "object", "properties": {}, "required": []}}],
            "edges": [],
        },
    })
    assert resp.status_code == 201
    wf_id = resp.json()["id"]

    resp2 = await client.get("/api/v1/workflows")
    assert resp2.status_code == 200
    assert len(resp2.json()) >= 1

    resp3 = await client.get(f"/api/v1/workflows/{wf_id}")
    assert resp3.status_code == 200
    assert resp3.json()["name"] == "test-wf"


@pytest.mark.asyncio
async def test_create_and_get_run(client):
    wf = await client.post("/api/v1/workflows", json={
        "name": "wf", "config": {
            "nodes": [{"id": "n1", "type": "script",
                       "command": "echo test",
                       "timeout_seconds": 60,
                       "output": {"type": "object", "properties": {}, "required": []}}],
            "edges": [],
        },
    })
    wf_id = wf.json()["id"]

    resp = await client.post("/api/v1/runs", json={"workflow_id": wf_id, "inputs": {}})
    assert resp.status_code == 201
    run_id = resp.json()["id"]
    assert resp.json()["status"] in ("pending", "running")

    detail = await client.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id

    # Wait for the background run executor to finish so it doesn't race
    # with engine.dispose() during fixture teardown.
    import asyncio
    for _ in range(50):  # 5-second timeout
        check = await client.get(f"/api/v1/runs/{run_id}")
        status = check.json()["status"]
        if status in ("completed", "failed"):
            break
        await asyncio.sleep(0.1)
    else:
        # If still running, at least give it a moment before teardown
        await asyncio.sleep(0.5)


@pytest.mark.asyncio
async def test_create_and_toggle_cron(client):
    wf = await client.post("/api/v1/workflows", json={
        "name": "wf", "config": {
            "nodes": [{"id": "n1", "type": "script",
                       "command": "echo test",
                       "timeout_seconds": 60,
                       "output": {"type": "object", "properties": {}, "required": []}}],
            "edges": [],
        },
    })
    wf_id = wf.json()["id"]

    resp = await client.post("/api/v1/cron", json={"workflow_id": wf_id, "cron_expr": "0 9 * * *"})
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    toggle = await client.post(f"/api/v1/cron/{job_id}/toggle")
    assert toggle.status_code == 200
    assert toggle.json()["enabled"] is False


@pytest.mark.asyncio
async def test_404_handling(client):
    resp = await client.get("/api/v1/workflows/nonexistent")
    assert resp.status_code == 404
    resp2 = await client.get("/api/v1/runs/nonexistent")
    assert resp2.status_code == 404
