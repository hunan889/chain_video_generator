"""
E2E tests for Prompt Optimization Settings.

Coverage:
  === Settings API ===
  1. GET returns defaults
  2. PUT updates values
  3. PUT ignores unknown keys
  4. PUT partial update
  5. PUT type coercion (bool/int boundary)

  === Workflow Logic (the LLM prompt optimization path) ===
  6. non-turbo + non_turbo=true  → auto_prompt=true
  7. non-turbo + non_turbo=false → auto_prompt=false
  8. turbo + long prompt          → auto_prompt=false (turbo default)
  9. turbo + short prompt (< min_chars) → auto_prompt=true (forced on)
  10. turbo + short prompt but min_chars lowered → auto_prompt=false (threshold bypassed)

  === UI ===
  11. UI loads settings from API
  12. UI saves settings
  13. UI persists after reload
  14. UI save shows feedback

Run:
    python tests/e2e_prompt_settings.py
"""

import json
import subprocess
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://148.153.121.44:8000"
API_URL = f"{BASE_URL}/api/v1/admin/settings"
WORKFLOW_URL = f"{BASE_URL}/api/v1/workflow/generate-advanced"
API_KEY = "wan22-default-key-change-me"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

DEFAULTS = {"prompt_optimize_min_chars": 20, "prompt_optimize_non_turbo": True}

SSH_HOST = "wan22-server"

# ---------------------------------------------------------------------------
# HTTP session with retry
# ---------------------------------------------------------------------------
_session = requests.Session()
_retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
_session.mount("http://", HTTPAdapter(max_retries=_retry))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_get() -> dict:
    r = _session.get(API_URL, headers=HEADERS, timeout=30)
    assert r.status_code == 200, f"GET failed: {r.status_code} {r.text}"
    return r.json()


def api_put(data: dict) -> dict:
    r = _session.put(API_URL, headers=HEADERS, json=data, timeout=30)
    assert r.status_code == 200, f"PUT failed: {r.status_code} {r.text}"
    return r.json()


def restore_defaults():
    api_put(DEFAULTS)


def submit_workflow(prompt: str, turbo: bool) -> str:
    """Submit a t2v workflow and return the workflow_id."""
    payload = {
        "mode": "t2v",
        "user_prompt": prompt,
        "turbo": turbo,
    }
    r = _session.post(WORKFLOW_URL, headers=HEADERS, json=payload, timeout=30)
    assert r.status_code == 200, f"POST workflow failed: {r.status_code} {r.text}"
    data = r.json()
    wf_id = data.get("workflow_id")
    assert wf_id, f"No workflow_id in response: {data}"
    return wf_id


def cancel_workflow(wf_id: str):
    """Cancel a workflow to avoid wasting GPU."""
    try:
        _session.post(f"{BASE_URL}/api/v1/workflow/{wf_id}/cancel",
                      headers=HEADERS, timeout=15)
    except Exception:
        pass


def redis_get_internal_config(wf_id: str) -> dict:
    """SSH to remote and read internal_config from Redis for a workflow."""
    cmd = f'redis-cli HGET workflow:{wf_id} internal_config'
    result = subprocess.run(
        ["ssh", SSH_HOST, cmd],
        capture_output=True, text=True, timeout=15,
    )
    raw = result.stdout.strip()
    assert raw and raw != "(nil)", f"No internal_config in Redis for {wf_id}: {raw}"
    return json.loads(raw)


def get_auto_prompt(wf_id: str) -> bool:
    """Extract stage1_prompt_analysis.auto_prompt from a workflow's internal_config."""
    ic = redis_get_internal_config(wf_id)
    stage1 = ic.get("stage1_prompt_analysis", {})
    return stage1.get("auto_prompt", False)


# ---------------------------------------------------------------------------
# Settings API Tests
# ---------------------------------------------------------------------------

def test_api_get_defaults():
    restore_defaults()
    data = api_get()
    assert data["prompt_optimize_min_chars"] == 20
    assert data["prompt_optimize_non_turbo"] is True
    print("  PASS  test_api_get_defaults")


def test_api_put_updates():
    result = api_put({"prompt_optimize_min_chars": 50, "prompt_optimize_non_turbo": False})
    assert result["prompt_optimize_min_chars"] == 50
    assert result["prompt_optimize_non_turbo"] is False
    data = api_get()
    assert data["prompt_optimize_min_chars"] == 50
    assert data["prompt_optimize_non_turbo"] is False
    print("  PASS  test_api_put_updates")


def test_api_put_ignores_unknown_keys():
    restore_defaults()
    result = api_put({
        "prompt_optimize_min_chars": 30,
        "unknown_key": "ignored",
        "another_bad": 999,
    })
    assert result["prompt_optimize_min_chars"] == 30
    assert "unknown_key" not in result
    assert "another_bad" not in result
    assert result["prompt_optimize_non_turbo"] is True
    print("  PASS  test_api_put_ignores_unknown_keys")


def test_api_put_partial_update():
    restore_defaults()
    api_put({"prompt_optimize_min_chars": 100})
    data = api_get()
    assert data["prompt_optimize_min_chars"] == 100
    assert data["prompt_optimize_non_turbo"] is True

    api_put({"prompt_optimize_non_turbo": False})
    data = api_get()
    assert data["prompt_optimize_min_chars"] == 100
    assert data["prompt_optimize_non_turbo"] is False
    print("  PASS  test_api_put_partial_update")


def test_api_put_type_coercion():
    api_put({"prompt_optimize_non_turbo": True})
    assert api_get()["prompt_optimize_non_turbo"] is True

    api_put({"prompt_optimize_non_turbo": False})
    assert api_get()["prompt_optimize_non_turbo"] is False

    api_put({"prompt_optimize_min_chars": 0})
    assert api_get()["prompt_optimize_min_chars"] == 0

    api_put({"prompt_optimize_min_chars": 500})
    assert api_get()["prompt_optimize_min_chars"] == 500
    print("  PASS  test_api_put_type_coercion")


# ---------------------------------------------------------------------------
# Workflow Logic Tests
# ---------------------------------------------------------------------------

def test_workflow_non_turbo_auto_prompt_on():
    """6. non-turbo + non_turbo=true → auto_prompt should be True."""
    api_put({"prompt_optimize_non_turbo": True})
    wf_id = submit_workflow("A cat walking in the garden on a sunny day with birds", turbo=False)
    time.sleep(1)
    try:
        auto = get_auto_prompt(wf_id)
        assert auto is True, f"Expected auto_prompt=True, got {auto}"
    finally:
        cancel_workflow(wf_id)
    print("  PASS  test_workflow_non_turbo_auto_prompt_on")


def test_workflow_non_turbo_auto_prompt_off():
    """7. non-turbo + non_turbo=false → auto_prompt should be False."""
    api_put({"prompt_optimize_non_turbo": False})
    wf_id = submit_workflow("A cat walking in the garden on a sunny day with birds", turbo=False)
    time.sleep(1)
    try:
        auto = get_auto_prompt(wf_id)
        assert auto is False, f"Expected auto_prompt=False, got {auto}"
    finally:
        cancel_workflow(wf_id)
    print("  PASS  test_workflow_non_turbo_auto_prompt_off")


def test_workflow_turbo_long_prompt_no_override():
    """8. turbo + long prompt (>min_chars) → auto_prompt stays False (turbo default)."""
    api_put({"prompt_optimize_min_chars": 20, "prompt_optimize_non_turbo": True})
    long_prompt = "A beautiful sunset over the ocean with waves crashing on the shore and seagulls flying"
    assert len(long_prompt) >= 20, f"Prompt too short: {len(long_prompt)}"
    wf_id = submit_workflow(long_prompt, turbo=True)
    time.sleep(1)
    try:
        auto = get_auto_prompt(wf_id)
        assert auto is False, f"Expected auto_prompt=False for turbo+long prompt, got {auto}"
    finally:
        cancel_workflow(wf_id)
    print("  PASS  test_workflow_turbo_long_prompt_no_override")


def test_workflow_turbo_short_prompt_forced_on():
    """9. turbo + short prompt (< min_chars=50) → auto_prompt forced True."""
    api_put({"prompt_optimize_min_chars": 50})
    short_prompt = "a cat dancing"  # 13 chars < 50
    assert len(short_prompt) < 50
    wf_id = submit_workflow(short_prompt, turbo=True)
    time.sleep(1)
    try:
        auto = get_auto_prompt(wf_id)
        assert auto is True, f"Expected auto_prompt=True for short prompt < min_chars=50, got {auto}"
    finally:
        cancel_workflow(wf_id)
    print("  PASS  test_workflow_turbo_short_prompt_forced_on")


def test_workflow_turbo_short_prompt_threshold_lowered():
    """10. turbo + 13-char prompt but min_chars=5 → auto_prompt stays False (13 >= 5)."""
    api_put({"prompt_optimize_min_chars": 5})
    prompt = "a cat dancing"  # 13 chars >= 5
    assert len(prompt) >= 5
    wf_id = submit_workflow(prompt, turbo=True)
    time.sleep(1)
    try:
        auto = get_auto_prompt(wf_id)
        assert auto is False, f"Expected auto_prompt=False (13 >= min_chars=5), got {auto}"
    finally:
        cancel_workflow(wf_id)
    print("  PASS  test_workflow_turbo_short_prompt_threshold_lowered")


# ---------------------------------------------------------------------------
# UI Helpers
# ---------------------------------------------------------------------------

def _goto_settings(page):
    page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    with page.expect_response(
        lambda r: "/admin/settings" in r.url and r.request.method == "GET",
        timeout=15000,
    ):
        page.click('.main-tab[data-main="settings"]')


# ---------------------------------------------------------------------------
# UI Tests (Playwright)
# ---------------------------------------------------------------------------

def test_ui_loads_settings(page):
    api_put({"prompt_optimize_min_chars": 42, "prompt_optimize_non_turbo": False})
    _goto_settings(page)
    expect(page.locator("#promptMinChars")).to_have_value("42", timeout=5000)
    expect(page.locator("#promptNonTurbo")).not_to_be_checked(timeout=5000)
    print("  PASS  test_ui_loads_settings")


def test_ui_saves_settings(page):
    restore_defaults()
    _goto_settings(page)
    expect(page.locator("#promptMinChars")).to_have_value("20", timeout=5000)
    page.fill("#promptMinChars", "88")
    page.uncheck("#promptNonTurbo")
    with page.expect_response(
        lambda r: "/admin/settings" in r.url and r.request.method == "PUT",
        timeout=15000,
    ) as resp_info:
        page.click("text=保存")
    assert resp_info.value.status == 200
    data = api_get()
    assert data["prompt_optimize_min_chars"] == 88
    assert data["prompt_optimize_non_turbo"] is False
    print("  PASS  test_ui_saves_settings")


def test_ui_persists_after_reload(page):
    api_put({"prompt_optimize_min_chars": 77, "prompt_optimize_non_turbo": True})
    _goto_settings(page)
    expect(page.locator("#promptMinChars")).to_have_value("77", timeout=5000)
    expect(page.locator("#promptNonTurbo")).to_be_checked(timeout=5000)
    _goto_settings(page)
    expect(page.locator("#promptMinChars")).to_have_value("77", timeout=5000)
    expect(page.locator("#promptNonTurbo")).to_be_checked(timeout=5000)
    print("  PASS  test_ui_persists_after_reload")


def test_ui_save_shows_feedback(page):
    restore_defaults()
    _goto_settings(page)
    expect(page.locator("#promptMinChars")).to_have_value("20", timeout=5000)
    msg = page.locator("#promptSettingsMsg")
    expect(msg).to_be_hidden()
    with page.expect_response(
        lambda r: "/admin/settings" in r.url and r.request.method == "PUT",
        timeout=15000,
    ):
        page.click("text=保存")
    expect(msg).to_be_visible(timeout=3000)
    assert "已保存" in msg.text_content()
    print("  PASS  test_ui_save_shows_feedback")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    passed = 0
    failed = 0
    errors = []

    def _run(tests, label, page=None):
        nonlocal passed, failed
        print(f"\n=== {label} ===")
        for t in tests:
            try:
                t(page) if page else t()
                passed += 1
            except Exception as e:
                failed += 1
                errors.append((t.__name__, str(e)))
                print(f"  FAIL  {t.__name__}: {e}")

    _run([
        test_api_get_defaults,
        test_api_put_updates,
        test_api_put_ignores_unknown_keys,
        test_api_put_partial_update,
        test_api_put_type_coercion,
    ], "Settings API Tests")

    _run([
        test_workflow_non_turbo_auto_prompt_on,
        test_workflow_non_turbo_auto_prompt_off,
        test_workflow_turbo_long_prompt_no_override,
        test_workflow_turbo_short_prompt_forced_on,
        test_workflow_turbo_short_prompt_threshold_lowered,
    ], "Workflow Logic Tests")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        _run([
            test_ui_loads_settings,
            test_ui_saves_settings,
            test_ui_persists_after_reload,
            test_ui_save_shows_feedback,
        ], "UI Tests (Playwright)", page=page)
        browser.close()

    try:
        restore_defaults()
    except Exception:
        pass

    total = passed + failed
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print(f"{'='*50}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit(main())
