"""E2E tests — Cron Jobs page."""

import json
import pytest
from playwright.sync_api import expect
from conftest import init_page
from helpers import create_test_workflow, create_test_cron

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _setup(api_client, page, server_url):
    wf_id = create_test_workflow(api_client)
    create_test_cron(api_client, wf_id)
    init_page(page, server_url)
    # Navigate to cron
    page.evaluate(
        "document.querySelector('[x-data]')._x_dataStack[0].page = 'cron'"
    )
    page.wait_for_timeout(1000)
    # Inject cron data
    resp = api_client.get("/api/v1/cron")
    cron_json = json.dumps(resp.json())
    page.evaluate(f"""
        document.querySelector('[x-data]')._x_dataStack[0].cronJobs = {cron_json};
    """)
    page.wait_for_timeout(1000)


def test_cron_page_content(page):
    """Cron page should show the cron expression in the table."""
    main_text = page.locator("main").inner_text()
    assert "0 9 * * *" in main_text, f"Cron expr not found. Content: {main_text[:500]}"


def test_cron_toggle_via_api(page, api_client):
    """Toggle cron via API, verify UI updates."""
    resp = api_client.get("/api/v1/cron")
    job_id = resp.json()[0]["id"]

    # Toggle disable
    api_client.post(f"/api/v1/cron/{job_id}/toggle")
    resp = api_client.get("/api/v1/cron")

    # Inject updated data
    cron_json = json.dumps(resp.json())
    page.evaluate(f"""
        document.querySelector('[x-data]')._x_dataStack[0].cronJobs = {cron_json};
    """)
    page.wait_for_timeout(500)

    main_text = page.locator("main").inner_text()
    # After toggle, status should show "Paused" (was "Active")
    assert "Paused" in main_text
