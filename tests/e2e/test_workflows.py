"""E2E tests — Workflows page."""

import pytest
from playwright.sync_api import expect
from conftest import init_page


@pytest.fixture(autouse=True)
def _setup(page, server_url):
    init_page(page, server_url)
    # Navigate to workflows by setting Alpine page state
    page.evaluate(
        "document.querySelector('[x-data]')._x_dataStack[0].page = 'workflows'"
    )
    page.wait_for_timeout(2000)


def test_workflows_page_loads(page):
    """Workflows section should be visible."""
    wf_section = page.locator("div[x-show=\"page === 'workflows'\"]")
    # Check it's not hidden (Alpine x-show removes display:none when active)
    expect(wf_section).not_to_have_attribute("style", "display: none")


def test_create_button_visible(page):
    """Create Workflow button should be visible."""
    btn = page.locator("button", has_text="Create Workflow")
    expect(btn).to_be_visible()


def test_create_workflow_via_api_and_verify_ui(page, api_client):
    """Create workflow via API, verify it appears in UI after loading."""
    # Create via API
    api_client.post("/api/v1/workflows", json={
        "name": "e2e-ui-wf",
        "config": {
            "nodes": [{
                "id": "n1", "type": "script",
                "script": {"module": "m", "function": "f", "args": {}},
                "output": {"type": "object", "properties": {}, "required": []},
            }],
            "edges": [],
        },
    })

    # Load workflows data into Alpine
    import json
    resp = api_client.get("/api/v1/workflows")
    workflows_json = json.dumps(resp.json())
    page.evaluate(f"""
        const data = document.querySelector('[x-data]')._x_dataStack[0];
        data.workflows = {workflows_json};
    """)
    page.wait_for_timeout(500)

    # Verify table shows the workflow name
    main_text = page.locator("main").inner_text()
    assert "e2e-ui-wf" in main_text
