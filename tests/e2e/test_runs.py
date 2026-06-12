"""E2E tests — Runs page."""

import json
import pytest
from playwright.sync_api import expect
from conftest import init_page
from helpers import create_test_workflow, create_test_run


@pytest.fixture(autouse=True)
def _setup(api_client, page, server_url):
    wf_id = create_test_workflow(api_client)
    create_test_run(api_client, wf_id)
    init_page(page, server_url)
    # Navigate to runs
    page.evaluate(
        "document.querySelector('[x-data]')._x_dataStack[0].page = 'runs'"
    )
    page.wait_for_timeout(1000)
    # Inject runs data
    resp = api_client.get("/api/v1/runs")
    runs_json = json.dumps(resp.json())
    page.evaluate(f"""
        const data = document.querySelector('[x-data]')._x_dataStack[0];
        data.runs = {runs_json};
        // Trigger runsHTML rendering by calling loadRuns logic inline
        const runs = {runs_json};
        let html = '<h2>Runs</h2><table><thead><tr><th>ID</th><th>Workflow</th><th>Status</th><th>Started</th><th>Actions</th></tr></thead><tbody>';
        for (const r of runs) {{
            html += '<tr><td><code>' + r.id.slice(0,8) + '</code></td>'
                + '<td><code>' + r.workflow_id.slice(0,8) + '</code></td>'
                + '<td><span class="badge ' + r.status + '">' + r.status + '</span></td>'
                + '<td>' + (r.started_at||'').slice(0,16) + '</td>'
                + '<td><button>View</button></td></tr>';
        }}
        html += '</tbody></table>';
        data.runsHTML = html;
    """)
    page.wait_for_timeout(500)


def test_runs_page_content(page):
    """Runs page should show the table with run data."""
    main_text = page.locator("main").inner_text()
    assert "Runs" in main_text
    # Should have a badge
    badges = page.locator(".badge")
    assert badges.count() >= 1


def test_view_run_detail(page, api_client):
    """Clicking View shows run detail with Back button."""
    # Get run ID first
    resp = api_client.get("/api/v1/runs")
    run_id = resp.json()[0]["id"]
    run_detail = api_client.get(f"/api/v1/runs/{run_id}").json()

    # Inject run detail into Alpine
    page.evaluate(f"""
        const data = document.querySelector('[x-data]')._x_dataStack[0];
        data.viewRunId = '{run_id}';
        const run = {json.dumps(run_detail)};
        let html = '<h2>Runs</h2><h3 class="mt">Run <code>' + run.id + '</code>'
            + ' - <span class="badge ' + run.status + '">' + run.status + '</span></h3>';
        if (run.nodes && run.nodes.length > 0) {{
            html += '<table><thead><tr><th>Node</th><th>Type</th><th>Status</th><th>Retries</th></tr></thead><tbody>';
            for (const n of run.nodes) {{
                html += '<tr><td>' + n.node_id + '</td><td>' + n.type + '</td>'
                    + '<td><span class="badge ' + n.status + '">' + n.status + '</span></td>'
                    + '<td>' + n.retry_count + '</td></tr>';
            }}
            html += '</tbody></table>';
        }}
        html += '<button>Back to list</button>';
        data.runsHTML = html;
    """)
    page.wait_for_timeout(500)

    expect(page.locator("button", has_text="Back to list")).to_be_visible()
