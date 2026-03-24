"""
Playwright E2E tests — verify modular UI split works correctly.

Tests target the remote server at http://148.153.121.44:8000
Run: pytest tests/test_ui_modules.py -v

NOTE: All tests share a single browser page (session-scoped) to avoid
overwhelming the remote server. Test ordering within each class matters.
"""
import pytest
from playwright.sync_api import expect

# Match the timeout from conftest
MODULE_LOAD_TIMEOUT = 30000


def _switch_to_image(page):
    """Helper: click image tab and wait for image module to be visible."""
    page.locator('.main-tab[data-main="image"]').click()
    page.wait_for_selector("#section-image", state="visible", timeout=MODULE_LOAD_TIMEOUT)


def _switch_to_video(page):
    """Helper: click video tab and wait for video module to be visible."""
    page.locator('.main-tab[data-main="video"]').click()
    page.wait_for_selector("#section-video", state="visible", timeout=MODULE_LOAD_TIMEOUT)


# ---------------------------------------------------------------------------
# 1. Shell Loading Tests
# ---------------------------------------------------------------------------

class TestShellLoads:
    """Verify the shell HTML (index.html) loads correctly."""

    def test_shell_loads(self, page):
        """Title, API key input, and all 8 main tabs exist."""
        expect(page).to_have_title("AI Studio")
        expect(page.locator("#apiKey")).to_be_attached()

        main_tabs = page.locator(".main-tab")
        expect(main_tabs).to_have_count(8)

        expected = ["video", "image", "workflow", "wf-history",
                    "favorites", "annotate", "poses", "settings"]
        for val in expected:
            expect(page.locator(f'.main-tab[data-main="{val}"]')).to_be_attached()

    def test_static_assets_load(self, page):
        """common.css, common.js, module-loader.js are loaded."""
        expect(page.locator('link[href="/static/css/common.css"]')).to_be_attached()
        expect(page.locator('script[src="/static/js/common.js"]')).to_be_attached()
        expect(page.locator('script[src="/static/js/module-loader.js"]')).to_be_attached()

        # Verify JS actually executed
        assert page.evaluate("typeof ModuleLoader") == "object"
        assert page.evaluate("typeof getKey") == "function"


# ---------------------------------------------------------------------------
# 2. Video Module Loading Tests
# ---------------------------------------------------------------------------

class TestVideoModule:
    """Verify the video module loads by default and its contents are correct."""

    VIDEO_SUB_TABS = ["t2v", "i2v", "chain", "query", "dlora", "civitai", "tts", "postproc"]

    def test_video_module_loads_by_default(self, page):
        """Video section exists with all 8 sub-tabs."""
        expect(page.locator("#section-video")).to_be_attached()

        for tab in self.VIDEO_SUB_TABS:
            expect(page.locator(f'#section-video .tab[data-tab="{tab}"]')).to_be_attached()

    def test_video_t2v_form_elements(self, page):
        """T2V form has all essential input elements."""
        elements = {
            "#t2v-prompt": "textarea",
            "#t2v-model": "select",
            "#t2v-w": "input",
            "#t2v-h": "input",
            "#t2v-duration": "input",
            "#t2v-steps": "input",
            "#t2v-seed": "input",
            "#t2v-cfg": "input",
            "#t2v-shift": "input",
            "#t2v-fps": "input",
            "#t2v-sched": "select",
        }
        for selector, tag in elements.items():
            loc = page.locator(selector)
            expect(loc).to_be_attached()
            assert loc.evaluate("el => el.tagName.toLowerCase()") == tag

    def test_video_i2v_tab_switch(self, page):
        """Click I2V tab: I2V panel visible, T2V panel hidden."""
        page.locator('#section-video .tab[data-tab="i2v"]').click()
        expect(page.locator("#panel-i2v")).to_be_visible()
        expect(page.locator("#panel-t2v")).not_to_be_visible()

    def test_video_sub_tab_switching(self, page):
        """Click each of the 8 sub-tabs, verify corresponding panel shows."""
        for tab in self.VIDEO_SUB_TABS:
            page.locator(f'#section-video .tab[data-tab="{tab}"]').click()
            expect(page.locator(f"#panel-{tab}")).to_be_visible()

            for other in self.VIDEO_SUB_TABS:
                if other != tab:
                    expect(page.locator(f"#panel-{other}")).not_to_be_visible()


# ---------------------------------------------------------------------------
# 3. Image Module Loading Tests
# ---------------------------------------------------------------------------

class TestImageModule:
    """Verify the image module loads and its contents are correct."""

    IMAGE_SUB_TABS = [
        "t2i", "i2i", "multiref", "sceneswap",
        "faceswap", "transfer", "zimage", "character", "history",
    ]

    @pytest.fixture(autouse=True)
    def _load_image_module(self, page):
        """Switch to image tab before each test in this class."""
        _switch_to_image(page)

    def test_image_module_loads(self, page):
        """Image section exists with all 9 sub-tabs."""
        expect(page.locator("#section-image")).to_be_attached()

        for tab in self.IMAGE_SUB_TABS:
            expect(
                page.locator(f'#section-image .tab[data-imgtab="{tab}"]')
            ).to_be_attached()

    def test_image_t2i_form_elements(self, page):
        """T2I form has essential input elements."""
        elements = ["#img-prompt", "#img-size", "#img-model", "#img-seed"]
        for selector in elements:
            expect(page.locator(selector)).to_be_attached()

    def test_image_sub_tab_switching(self, page):
        """Click each of the 9 sub-tabs, verify corresponding panel shows."""
        for tab in self.IMAGE_SUB_TABS:
            page.locator(f'#section-image .tab[data-imgtab="{tab}"]').click()
            expect(page.locator(f"#imgpanel-{tab}")).to_be_visible()

            for other in self.IMAGE_SUB_TABS:
                if other != tab:
                    expect(page.locator(f"#imgpanel-{other}")).not_to_be_visible()


# ---------------------------------------------------------------------------
# 4. Tab Switching and Module Isolation Tests
# ---------------------------------------------------------------------------

class TestTabSwitching:
    """Verify switching between main tabs and module content isolation."""

    def test_switch_video_to_image_and_back(self, page):
        """Switch video -> image -> video; video module content still present."""
        expect(page.locator("#section-video")).to_be_attached()

        _switch_to_image(page)
        expect(page.locator("#section-image")).to_be_attached()

        _switch_to_video(page)
        expect(page.locator("#section-video")).to_be_attached()
        expect(page.locator("#t2v-prompt")).to_be_attached()

    def test_module_content_isolation(self, page):
        """When image module active, video is hidden; and vice versa."""
        expect(page.locator("#section-video")).to_be_visible()

        # Switch to image — video section hidden by ModuleLoader
        _switch_to_image(page)
        expect(page.locator("#section-video")).not_to_be_visible()

        # Switch back — image section hidden
        _switch_to_video(page)
        expect(page.locator("#section-image")).not_to_be_visible()


# ---------------------------------------------------------------------------
# 5. Iframe Lazy Loading Tests
# ---------------------------------------------------------------------------

class TestIframeLazyLoading:
    """Verify iframes don't load until their tab is clicked."""

    def test_iframe_lazy_loading(self, page):
        """Iframe elements that haven't been clicked have no src attribute."""
        for iframe_id in ["iframe-wf-history", "iframe-favorites", "iframe-annotate"]:
            src = page.locator(f"#{iframe_id}").get_attribute("src")
            assert src is None or src == "", \
                f"#{iframe_id} should not have src before its tab is clicked, got: {src}"

    def test_iframe_loads_on_click(self, page):
        """Clicking '高级工作流' tab sets iframe-workflow src."""
        page.locator('.main-tab[data-main="workflow"]').click()
        page.wait_for_timeout(500)

        src = page.locator("#iframe-workflow").get_attribute("src")
        assert src and "advanced_workflow" in src, \
            f"Expected iframe src to contain 'advanced_workflow', got: {src}"


# ---------------------------------------------------------------------------
# 6. Shared Infrastructure Tests
# ---------------------------------------------------------------------------

class TestSharedInfrastructure:
    """Verify common.js globals and localStorage persistence."""

    def test_global_functions_available(self, page):
        """Key global functions from common.js are accessible."""
        functions = ["getKey", "loadLoras", "addTask", "pollTask", "formatDuration"]
        for fn in functions:
            # Use typeof without window. prefix because const/let
            # declarations don't become window properties
            result = page.evaluate(f"typeof {fn}")
            assert result == "function", \
                f"Expected {fn} to be a function, got: {result}"

    def test_module_loader_available(self, page):
        """ModuleLoader object is available with expected methods."""
        assert page.evaluate("typeof ModuleLoader") == "object"
        for method in ["load", "registerCleanup", "invalidateCache"]:
            assert page.evaluate(f"typeof ModuleLoader.{method}") == "function"

    def test_api_key_persistence(self, page):
        """Set API key -> change event -> verify localStorage."""
        test_key = "test-e2e-key-12345"

        # Switch to settings tab to make #apiKey visible
        page.locator('.main-tab[data-main="settings"]').click()
        page.wait_for_selector("#section-settings.active", timeout=5000)

        page.locator("#apiKey").fill(test_key)
        page.locator("#apiKey").dispatch_event("change")

        stored = page.evaluate("localStorage.getItem('wan22_api_key')")
        assert stored == test_key

        # Verify getKey() returns the current input value
        assert page.evaluate("getKey()") == test_key

        # Cleanup
        page.evaluate("localStorage.removeItem('wan22_api_key')")

        # Switch back to video so page fixture state is consistent
        page.locator('.main-tab[data-main="video"]').click()
        page.wait_for_selector("#section-video", state="visible", timeout=MODULE_LOAD_TIMEOUT)


# ---------------------------------------------------------------------------
# 7. Module Cache Verification Tests
# ---------------------------------------------------------------------------

class TestModuleCache:
    """Verify ModuleLoader caching behavior."""

    def test_video_module_is_cached(self, page):
        """Video module should be in ModuleLoader cache (loaded on startup)."""
        cached = page.evaluate("Object.keys(ModuleLoader._cache)")
        assert "video" in cached, "video module should be cached after loading"

    def test_image_module_cached_after_click(self, page):
        """Clicking image tab loads and caches the image module."""
        # Invalidate image cache to test fresh loading
        page.evaluate("ModuleLoader.invalidateCache('image')")

        cached_before = page.evaluate("Object.keys(ModuleLoader._cache)")
        assert "image" not in cached_before, "image should not be cached after invalidation"

        _switch_to_image(page)

        cached_after = page.evaluate("Object.keys(ModuleLoader._cache)")
        assert "image" in cached_after, \
            "Expected 'image' in ModuleLoader._cache after clicking image tab"
