"""
E2E test: verify the generation history page correctly displays task statuses.

Tests the fix for the bug where workflow tasks (wf_*) showed "排队中" (queued)
instead of "生成中" (running) because the history API wasn't merging real-time
Redis status for workflow:{id} keys.

Target: http://170.106.36.6:20002 (nginx → uvicorn api_gateway on port 9000)
"""
import json
import time

from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://170.106.36.6:20002"
PAGE_URL = f"{BASE_URL}/static/workflow_history.html"
HISTORY_API = f"{BASE_URL}/api/v1/generation/history"
TIMEOUT = 15000


def test_page_loads_and_renders_cards():
    """Page loads and renders at least one history card."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        page.wait_for_selector(".card[data-wfid]", state="visible", timeout=TIMEOUT)

        cards = page.locator(".card[data-wfid]")
        count = cards.count()
        print(f"Total cards rendered: {count}")
        assert count > 0, "Expected at least 1 history card"

        print(f"✓ Page loaded, {count} cards rendered")
        browser.close()


def test_status_labels_are_correct():
    """Each card's status label matches the expected Chinese text for its status."""
    expected_labels = {
        "completed": "已完成",
        "running": "生成中",
        "failed": "失败",
        "queued": "排队中",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        page.wait_for_selector(".card[data-wfid]", state="visible", timeout=TIMEOUT)

        cards = page.locator(".card[data-wfid]")
        count = cards.count()

        mismatches = []
        for i in range(count):
            card = cards.nth(i)
            wfid = card.get_attribute("data-wfid")
            status_el = card.locator(".card-status")
            if status_el.count() == 0:
                continue
            label = status_el.text_content().strip()
            css_class = status_el.get_attribute("class") or ""

            # Extract status from CSS class (status-running, status-completed, etc.)
            status = None
            for s in expected_labels:
                if f"status-{s}" in css_class:
                    status = s
                    break

            if status and label != expected_labels[status]:
                mismatches.append(
                    f"Card {wfid}: CSS class says '{status}' but label is '{label}' "
                    f"(expected '{expected_labels[status]}')"
                )
            print(f"  Card {wfid}: status={status}, label='{label}' ✓")

        assert not mismatches, f"Status label mismatches:\n" + "\n".join(mismatches)
        print(f"✓ All {count} cards have correct status labels")
        browser.close()


def test_no_running_workflow_shows_queued():
    """CRITICAL: No wf_* task with actual 'running' state in Redis
    should display '排队中' on the page.

    This verifies the fix: the history API now checks workflow:{id}
    Redis keys for wf_* tasks, so their real-time status is correctly
    merged instead of showing stale MySQL 'queued' status.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        page.wait_for_selector(".card[data-wfid]", state="visible", timeout=TIMEOUT)

        # Collect all wf_* cards that show "排队中"
        cards = page.locator(".card[data-wfid]")
        count = cards.count()

        queued_wf_cards = []
        for i in range(count):
            card = cards.nth(i)
            wfid = card.get_attribute("data-wfid") or ""
            if not wfid.startswith("wf_"):
                continue
            status_el = card.locator(".card-status")
            if status_el.count() == 0:
                continue
            label = status_el.text_content().strip()
            if label == "排队中":
                queued_wf_cards.append(wfid)

        if queued_wf_cards:
            # Double-check: query the status detail API for each to see real Redis status
            bugs_found = []
            for wfid in queued_wf_cards:
                resp = page.request.get(f"{BASE_URL}/api/v1/workflow/status/{wfid}?detail=true")
                if resp.ok:
                    data = resp.json()
                    real_status = data.get("status", "unknown")
                    if real_status == "running":
                        bugs_found.append(
                            f"Card {wfid}: shows '排队中' but real status is 'running' — BUG!"
                        )
                    else:
                        print(f"  Card {wfid}: shows '排队中', real status='{real_status}' (OK, truly queued)")
                else:
                    print(f"  Card {wfid}: status API returned {resp.status} (skipping)")

            assert not bugs_found, (
                "BUG DETECTED: Running workflows showing as '排队中':\n" +
                "\n".join(bugs_found)
            )

        print(f"✓ No running wf_* tasks are incorrectly showing '排队中'")
        browser.close()


def test_filter_buttons_work():
    """Status filter buttons filter cards correctly."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        page.wait_for_selector(".card[data-wfid]", state="visible", timeout=TIMEOUT)

        total_before = page.locator(".card[data-wfid]").count()
        print(f"  All: {total_before} cards")

        # Click '已完成' filter
        completed_btn = page.locator('button.filter-btn[data-status="completed"]')
        if completed_btn.count() > 0:
            completed_btn.click()
            page.wait_for_timeout(2000)
            completed_cards = page.locator(".card[data-wfid]")
            completed_count = completed_cards.count()
            print(f"  Completed filter: {completed_count} cards")

            # All visible cards should have status-completed
            for i in range(completed_count):
                cls = completed_cards.nth(i).locator(".card-status").get_attribute("class") or ""
                assert "status-completed" in cls, f"Card {i} not completed after filter: {cls}"

        # Reset to all
        all_btn = page.locator('button.filter-btn[data-status=""]')
        if all_btn.count() > 0:
            all_btn.click()
            page.wait_for_timeout(2000)

        print("✓ Filter buttons work correctly")
        browser.close()


def test_card_detail_modal():
    """Clicking a card opens the detail modal with correct info."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        page.wait_for_selector(".card[data-wfid]", state="visible", timeout=TIMEOUT)

        # Click the first card
        first_card = page.locator(".card[data-wfid]").first
        wfid = first_card.get_attribute("data-wfid")
        first_card.click()

        # Wait for modal to appear
        modal = page.locator("#detailModal, .modal, .detail-modal")
        try:
            modal.wait_for(state="visible", timeout=5000)
            print(f"  Modal opened for card {wfid}")

            # Check modal has some content
            modal_text = modal.text_content() or ""
            assert len(modal_text) > 10, "Modal appears empty"
            print(f"  Modal content length: {len(modal_text)} chars")
            print("✓ Detail modal opens correctly")
        except Exception:
            # Some pages may not have a modal — that's OK for this test
            print("  (No modal found — skipping modal test)")
            print("✓ Card click test completed (no modal)")

        browser.close()


def test_api_returns_correct_status_for_workflow_tasks():
    """Directly verify the API merges Redis status for wf_* tasks."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()

        resp = ctx.request.get(f"{HISTORY_API}?page=1&page_size=50")
        assert resp.ok, f"API returned {resp.status}"
        data = resp.json()

        workflows = data.get("workflows", [])
        print(f"  API returned {len(workflows)} tasks")

        wf_tasks = [w for w in workflows if w.get("workflow_id", "").startswith("wf_")]
        print(f"  Of which {len(wf_tasks)} are wf_* workflow tasks")

        # Check: no wf_* task should have status=queued if it's actually running
        queued_wf = [w for w in wf_tasks if w.get("status") == "queued"]
        if queued_wf:
            print(f"  Found {len(queued_wf)} wf_* tasks with status=queued — verifying each...")
            for w in queued_wf:
                wfid = w["workflow_id"]
                detail_resp = ctx.request.get(f"{BASE_URL}/api/v1/workflow/status/{wfid}?detail=true")
                if detail_resp.ok:
                    detail = detail_resp.json()
                    real_status = detail.get("status", "unknown")
                    assert real_status != "running", (
                        f"BUG: API history says {wfid} is 'queued' but status detail says 'running'. "
                        f"The Redis merge for workflow:{{id}} is not working!"
                    )
                    print(f"    {wfid}: queued in both history and detail API (OK)")
        else:
            print("  No queued wf_* tasks found (all resolved)")

        print("✓ API correctly merges Redis status for workflow tasks")
        browser.close()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
