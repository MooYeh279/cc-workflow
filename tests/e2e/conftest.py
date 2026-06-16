"""E2E test fixtures — FastAPI server lifecycle + Playwright browser."""

import os
import subprocess
import sys
import tempfile
import time

import httpx
import pytest


@pytest.fixture(scope="session")
def server_url():
    """Start WFlow FastAPI server on a test port, return the base URL."""
    port = 18100
    url = f"http://localhost:{port}"

    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    db_path = os.path.join(project_root, "data", "test_e2e.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    abs_db_path = os.path.abspath(db_path).replace("\\", "/")
    env = os.environ.copy()
    env["WFLOW_DB_URL"] = f"sqlite+aiosqlite:///{abs_db_path}"

    stdout_f = tempfile.TemporaryFile(mode="w+")
    stderr_f = tempfile.TemporaryFile(mode="w+")

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "wflow.main:create_app",
            "--host", "localhost", "--port", str(port), "--factory",
        ],
        env=env,
        stdout=stdout_f,
        stderr=stderr_f,
        cwd=project_root,
    )

    deadline = time.time() + 30
    last_error = None
    while time.time() < deadline:
        time.sleep(0.3)
        if proc.poll() is not None:
            stderr_f.seek(0)
            stderr_text = stderr_f.read()
            raise RuntimeError(
                f"Server process exited with code {proc.returncode}.\n"
                f"stderr: {stderr_text[:3000]}"
            )
        try:
            resp = httpx.get(f"{url}/api/v1/status", timeout=2, trust_env=False)
            if resp.status_code == 200:
                break
        except Exception as e:
            last_error = e
    else:
        proc.kill()
        raise RuntimeError(f"Server failed to start within 30s. Last error: {last_error}")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture(scope="session")
def api_client(server_url):
    """httpx client for direct API setup (bypasses browser)."""
    return httpx.Client(base_url=server_url, trust_env=False)


def navigate_to(page, tab_name: str):
    """Navigate to a tab and load its data via direct API calls."""
    page.evaluate(f"""
        (() => {{
            const el = document.querySelector('[x-data]');
            el._x_dataStack[0].page = '{tab_name}';
        }})()
    """)
    page.wait_for_timeout(2000)


def init_page(page, server_url):
    """Navigate to WFlow and wait for Alpine to boot."""
    page.goto(server_url)
    page.wait_for_selector(".sidebar-nav", timeout=10000)
    page.wait_for_timeout(3000)
