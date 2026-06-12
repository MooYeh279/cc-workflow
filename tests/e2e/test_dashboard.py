"""E2E tests — Dashboard page."""

import pytest
from playwright.sync_api import expect
from conftest import init_page


@pytest.fixture(autouse=True)
def _setup(page, server_url):
    init_page(page, server_url)


def test_dashboard_loads(page):
    expect(page.locator("h1")).to_contain_text("WFlow")


def test_dashboard_shows_nav(page):
    nav = page.locator(".nav-links")
    expect(nav).to_be_visible()
    links = nav.locator("a")
    expect(links).to_have_count(4)
    links_text = [el.inner_text() for el in links.all()]
    for tab in ["Dashboard", "Workflows", "Runs", "Cron"]:
        assert tab in links_text, f"Missing nav link: {tab}"


def test_page_structure(page):
    expect(page.locator("h1")).to_be_visible()
    expect(page.locator("footer")).to_be_visible()


def test_dashboard_has_title(page):
    """Page title should contain WFlow."""
    expect(page).to_have_title("WFlow — Workflow Orchestrator")
