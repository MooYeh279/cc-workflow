"""Root test configuration — isolates E2E tests from async unit tests.
pytest-playwright and pytest-asyncio manage asyncio event loops in
mutually incompatible ways.  Running them in the same session causes
``RuntimeError: Runner.run() cannot be called from a running event loop``
on every async test.
This hook guarantees the two groups never mix:
    pytest              →  all unit / integration / API tests (default)
    pytest -m e2e       →  E2E tests only
"""
import pytest
def pytest_collection_modifyitems(config, items):
    """Skip the test group that doesn't match the current invocation."""
    marker_filter = config.getoption("-m", "")
    if "e2e" in marker_filter:
        skip = pytest.mark.skip(reason="Non-E2E excluded from E2E run")
        for item in items:
            if "e2e" not in item.keywords:
                item.add_marker(skip)
    else:
        skip = pytest.mark.skip(reason="Use -m e2e to run E2E tests")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip)
