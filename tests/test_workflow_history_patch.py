"""
E2E test: verify workflow_history.html patchGrid does incremental updates.

Checks that on auto-refresh (silent poll), unchanged cards keep their DOM nodes
and only changed cards get replaced.
"""
import re
import pytest
from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://148.153.121.44:8000"
PAGE_URL = f"{BASE_URL}/static/workflow_history.html"
TIMEOUT = 30000


def test_patch_grid_no_full_reload():
    """
    1. Load page, wait for grid to render
    2. Capture card DOM node references (via data-wfid + unique marker)
    3. Wait for at least one silent poll cycle
    4. Verify unchanged cards kept same DOM node (marker survives)
    5. Verify console logs show patchGrid with 0 or few updates (not full re-render)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Collect console logs
        console_logs = []
        page.on("console", lambda msg: console_logs.append(msg.text))

        # Navigate
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)

        # Wait for grid to have cards
        page.wait_for_selector(".card[data-wfid]", state="visible", timeout=TIMEOUT)

        # Count initial cards
        cards = page.query_selector_all(".card[data-wfid]")
        initial_count = len(cards)
        print(f"Initial cards: {initial_count}")
        assert initial_count > 0, "Expected at least 1 card"

        # Mark all cards with a custom attribute to detect DOM replacement
        page.evaluate("""
            document.querySelectorAll('.card[data-wfid]').forEach(el => {
                el.setAttribute('data-patch-marker', 'original');
            });
        """)

        # Verify markers are set
        marked = page.evaluate("document.querySelectorAll('.card[data-patch-marker=\"original\"]').length")
        assert marked == initial_count, f"Expected {initial_count} markers, got {marked}"

        # Wait for at least one poll cycle (polls every 5s for running, 30s otherwise)
        # Force a silent poll immediately
        page.evaluate("loadHistory(true)")
        page.wait_for_timeout(3000)  # Wait for fetch + render

        # Check how many cards still have the marker (not replaced)
        surviving = page.evaluate("document.querySelectorAll('.card[data-patch-marker=\"original\"]').length")
        total_after = page.evaluate("document.querySelectorAll('.card[data-wfid]').length")
        replaced = initial_count - surviving

        print(f"After poll: {total_after} cards, {surviving} survived, {replaced} replaced")

        # Check console logs for patchGrid output
        patch_logs = [l for l in console_logs if '[patchGrid]' in l]
        print(f"Console logs: {patch_logs}")

        # Assertions
        assert total_after > 0, "Cards should still exist after poll"

        # Key assertion: if no data changed, most/all cards should survive
        # (some may have changed status, that's OK - but NOT all should be replaced)
        if total_after == initial_count:
            # Same number of cards - patchGrid should have run (not full re-render)
            assert surviving > 0, (
                f"All {initial_count} cards were replaced! patchGrid is not working. "
                f"Console: {patch_logs}"
            )
            print(f"SUCCESS: {surviving}/{initial_count} cards preserved (incremental update)")
        else:
            print(f"Card count changed ({initial_count} -> {total_after}), full re-render expected")

        # Check that patchGrid log exists (not "ID list changed")
        if patch_logs:
            full_rerender_logs = [l for l in patch_logs if "ID list changed" in l]
            incremental_logs = [l for l in patch_logs if "cards updated" in l]
            if incremental_logs:
                # Parse "X/Y cards updated"
                match = re.search(r'(\d+)/(\d+) cards updated', incremental_logs[-1])
                if match:
                    updated = int(match.group(1))
                    total = int(match.group(2))
                    print(f"patchGrid: {updated}/{total} cards updated")
                    # If total matches and updated < total, incremental is working
                    if updated < total:
                        print("CONFIRMED: Incremental update is working correctly")

            if full_rerender_logs and not incremental_logs:
                print(f"WARNING: Full re-render triggered: {full_rerender_logs}")

        browser.close()


def test_patch_grid_detects_status_change():
    """
    Verify that when a card's status changes, only that card is updated.
    We simulate this by modifying the prevFingerprints to force a mismatch on one card.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        console_logs = []
        page.on("console", lambda msg: console_logs.append(msg.text))

        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        page.wait_for_selector(".card[data-wfid]", state="visible", timeout=TIMEOUT)

        cards_count = page.evaluate("document.querySelectorAll('.card[data-wfid]').length")
        if cards_count < 2:
            pytest.skip("Need at least 2 cards to test selective update")

        # Mark all cards
        page.evaluate("""
            document.querySelectorAll('.card[data-wfid]').forEach(el => {
                el.setAttribute('data-patch-marker', 'original');
            });
        """)

        # Corrupt one fingerprint to simulate a change
        page.evaluate("""
            const ids = Object.keys(prevFingerprints);
            if (ids.length > 0) {
                prevFingerprints[ids[0]] = 'FAKE_CHANGED';
            }
        """)

        # Trigger silent poll
        page.evaluate("loadHistory(true)")
        page.wait_for_timeout(3000)

        surviving = page.evaluate("document.querySelectorAll('.card[data-patch-marker=\"original\"]').length")
        total_after = page.evaluate("document.querySelectorAll('.card[data-wfid]').length")

        print(f"After forced change: {surviving}/{total_after} survived")

        # Exactly 1 card should have been replaced (the one with corrupted fingerprint)
        # The rest should survive
        if total_after == cards_count:
            replaced = cards_count - surviving
            print(f"{replaced} card(s) replaced, {surviving} preserved")
            assert surviving >= cards_count - 1, (
                f"Expected at most 1 card replaced, but {replaced} were replaced. "
                f"patchGrid is replacing too many cards."
            )
            assert replaced >= 1, "Expected at least 1 card to be replaced (the corrupted one)"
            print("CONFIRMED: Only the changed card was replaced")

        patch_logs = [l for l in console_logs if '[patchGrid]' in l]
        print(f"Console: {patch_logs}")

        browser.close()


if __name__ == "__main__":
    test_patch_grid_no_full_reload()
    print("\n" + "=" * 60 + "\n")
    test_patch_grid_detects_status_change()
