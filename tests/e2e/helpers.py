"""Shared E2E test helpers for API setup."""


def create_test_workflow(api_client):
    """Create a simple workflow via API and return its ID."""
    resp = api_client.post("/api/v1/workflows", json={
        "name": "e2e-test-wf",
        "config": {
            "nodes": [{
                "id": "start", "type": "script",
                "command": "python ./scripts/echo.py",
                "timeout_seconds": 60,
                "output": {"type": "object", "properties": {}, "required": []},
            }],
            "edges": [],
        },
    })
    assert resp.status_code == 201, f"Failed to create workflow: {resp.text}"
    return resp.json()["id"]


def create_test_run(api_client, workflow_id):
    """Create a run via API and return its ID."""
    resp = api_client.post("/api/v1/runs", json={
        "workflow_id": workflow_id, "inputs": {},
    })
    assert resp.status_code == 201, f"Failed to create run: {resp.text}"
    return resp.json()["id"]


def create_test_cron(api_client, workflow_id):
    """Create a cron job via API and return its ID."""
    resp = api_client.post("/api/v1/cron", json={
        "workflow_id": workflow_id, "cron_expr": "0 9 * * *",
    })
    assert resp.status_code == 201, f"Failed to create cron job: {resp.text}"
    return resp.json()["id"]
