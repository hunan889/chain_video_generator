"""
Playwright E2E test fixtures for UI module verification.

Uses a single shared page (session-scoped) to avoid overwhelming the
remote server with rapid connection open/close cycles.
"""
import time

import pytest


BASE_URL = "http://148.153.121.44:8000"

# Module loading can be slow over the network — use generous timeouts
MODULE_LOAD_TIMEOUT = 30000


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def shared_page(browser, base_url):
    """Single page shared across the entire test session.

    Retries navigation up to 3 times if the remote server resets.
    """
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
    )
    p = ctx.new_page()
    p.add_init_script("() => localStorage.removeItem('wan22_main_tab')")

    for attempt in range(3):
        try:
            p.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            p.wait_for_selector("#section-video", state="attached", timeout=MODULE_LOAD_TIMEOUT)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5)

    yield p

    p.close()
    ctx.close()


@pytest.fixture
def page(shared_page):
    """Per-test fixture: ensure video module is loaded (default state)."""
    if shared_page.locator("#section-video").count() == 0:
        shared_page.locator('.main-tab[data-main="video"]').click()
        shared_page.wait_for_selector(
            "#section-video", state="attached", timeout=MODULE_LOAD_TIMEOUT
        )
    return shared_page
