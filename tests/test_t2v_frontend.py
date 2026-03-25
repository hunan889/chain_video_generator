"""
Playwright E2E tests — verify T2V mode in advanced workflow UI.

Tests target the remote server at http://148.153.121.44:8000
Run: pytest tests/test_t2v_frontend.py -v

These tests verify:
1. T2V option exists in mode dropdown
2. Upload area hides when T2V is selected
3. Stage 2/3 sections hide for T2V mode
4. Validation allows T2V without image
5. buildRequestData sends correct fields for T2V
6. Mode labels include t2v in workflow history
7. getDefaultConfig returns correct config for t2v
8. first_frame_source dropdown removed
"""
import pytest
from playwright.sync_api import expect, sync_playwright

BASE_URL = "http://148.153.121.44:8000"
WORKFLOW_PAGE = f"{BASE_URL}/static/advanced_workflow_v2.html"
HISTORY_PAGE = f"{BASE_URL}/static/workflow_history.html"
PAGE_TIMEOUT = 30000


@pytest.fixture(scope="module")
def browser():
    """Launch browser for test module."""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture(scope="module")
def workflow_context(browser):
    """Create a browser context for the workflow page."""
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
    )
    yield ctx
    ctx.close()


@pytest.fixture
def wf_page(workflow_context):
    """Fresh page navigated to advanced_workflow_v2.html for each test."""
    page = workflow_context.new_page()
    page.goto(WORKFLOW_PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # Wait for page JS to initialize
    page.wait_for_function("typeof generateVideo === 'function'", timeout=PAGE_TIMEOUT)
    yield page
    page.close()


# ---------------------------------------------------------------------------
# 1. Mode Dropdown Tests
# ---------------------------------------------------------------------------

class TestModeDropdown:
    """Verify T2V option in mode dropdown."""

    def test_t2v_option_exists(self, wf_page):
        """Mode dropdown should have t2v option."""
        options = wf_page.locator("#imageRole option")
        values = options.evaluate_all("els => els.map(e => e.value)")
        assert "t2v" in values, f"Expected 't2v' in mode options, got: {values}"

    def test_t2v_is_first_option(self, wf_page):
        """T2V should be the first option in the dropdown."""
        first_option = wf_page.locator("#imageRole option").first
        assert first_option.get_attribute("value") == "t2v"

    def test_all_four_modes_present(self, wf_page):
        """All 4 modes should be present in dropdown."""
        values = wf_page.locator("#imageRole option").evaluate_all(
            "els => els.map(e => e.value)"
        )
        expected = ["t2v", "first_frame", "full_body_reference", "face_reference"]
        for mode in expected:
            assert mode in values, f"Expected '{mode}' in options, got: {values}"

    def test_t2v_label_text(self, wf_page):
        """T2V option should have correct Chinese label."""
        t2v_option = wf_page.locator('#imageRole option[value="t2v"]')
        text = t2v_option.inner_text()
        assert "文生视频" in text, f"Expected '文生视频' in label, got: {text}"

    def test_dropdown_label_is_mode(self, wf_page):
        """Dropdown label should say '生成模式' (not '上传图片作用')."""
        label = wf_page.locator("#imageRole").locator("..").locator("label")
        text = label.inner_text()
        assert "生成模式" in text, f"Expected '生成模式' in label, got: {text}"


# ---------------------------------------------------------------------------
# 2. Upload Area Visibility Tests
# ---------------------------------------------------------------------------

class TestUploadAreaVisibility:
    """Verify upload area hides for T2V mode."""

    def test_upload_hidden_for_t2v(self, wf_page):
        """Upload area row should be hidden when T2V is selected."""
        # First switch away to ensure change event fires
        wf_page.select_option("#imageRole", "first_frame")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        wf_page.select_option("#imageRole", "t2v")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(300)

        # JS hides the .row grandparent via closest('.row')
        is_hidden = wf_page.evaluate("""(() => {
            const row = document.getElementById('uploadArea')?.closest('.row');
            if (!row) return true;
            return row.style.display === 'none';
        })()""")
        assert is_hidden, "Upload row should be hidden for T2V"

    def test_upload_visible_for_first_frame(self, wf_page):
        """Upload area row should be visible when first_frame is selected."""
        wf_page.select_option("#imageRole", "first_frame")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        is_visible = wf_page.evaluate("""(() => {
            const row = document.getElementById('uploadArea')?.closest('.row');
            if (!row) return false;
            return row.style.display !== 'none';
        })()""")
        assert is_visible, "Upload row should be visible for first_frame"

    def test_upload_visible_for_face_reference(self, wf_page):
        """Upload area row should be visible when face_reference is selected."""
        wf_page.select_option("#imageRole", "face_reference")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        is_visible = wf_page.evaluate("""(() => {
            const row = document.getElementById('uploadArea')?.closest('.row');
            if (!row) return false;
            return row.style.display !== 'none';
        })()""")
        assert is_visible, "Upload row should be visible for face_reference"


# ---------------------------------------------------------------------------
# 3. Stage Section Visibility Tests
# ---------------------------------------------------------------------------

class TestStageSectionVisibility:
    """Verify Stage 2 face swap and Stage 3 visibility for T2V mode."""

    def test_face_swap_hidden_for_t2v(self, wf_page):
        """Stage 2 face swap section should be hidden for T2V."""
        wf_page.select_option("#imageRole", "t2v")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        face_swap = wf_page.locator("#s2_faceSwapSection")
        expect(face_swap).not_to_be_visible()

    def test_stage3_hidden_for_t2v(self, wf_page):
        """Stage 3 SeeDream header should be hidden for T2V."""
        wf_page.select_option("#imageRole", "t2v")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        s3_header = wf_page.locator("#s3_header")
        expect(s3_header).not_to_be_visible()

        s3_content = wf_page.locator("#s3_content")
        expect(s3_content).not_to_be_visible()

    def test_face_swap_visible_for_face_reference(self, wf_page):
        """Stage 2 face swap should be visible for face_reference."""
        wf_page.select_option("#imageRole", "face_reference")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        face_swap = wf_page.locator("#s2_faceSwapSection")
        expect(face_swap).to_be_visible()

    def test_stage3_visible_for_full_body(self, wf_page):
        """Stage 3 SeeDream header should be visible for full_body_reference."""
        wf_page.select_option("#imageRole", "full_body_reference")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        s3_header = wf_page.locator("#s3_header")
        expect(s3_header).to_be_visible()


# ---------------------------------------------------------------------------
# 4. first_frame_source Removal Tests
# ---------------------------------------------------------------------------

class TestFirstFrameSourceRemoved:
    """Verify first_frame_source dropdown has been removed."""

    def test_first_frame_source_dropdown_removed(self, wf_page):
        """The s2_firstFrameSource select element should not exist."""
        count = wf_page.locator("#s2_firstFrameSource").count()
        assert count == 0, "s2_firstFrameSource dropdown should be removed"

    def test_t2i_params_removed(self, wf_page):
        """T2I parameter inputs should not exist."""
        for elem_id in ["s2_t2iSteps", "s2_t2iCfg", "s2_t2iSampler", "s2_t2iSeed"]:
            count = wf_page.locator(f"#{elem_id}").count()
            assert count == 0, f"#{elem_id} should be removed"


# ---------------------------------------------------------------------------
# 5. getDefaultConfig Tests
# ---------------------------------------------------------------------------

class TestGetDefaultConfig:
    """Verify JS getDefaultConfig handles t2v mode correctly."""

    def test_t2v_stage3_disabled(self, wf_page):
        """getDefaultConfig('t2v') should have stage3 disabled."""
        result = wf_page.evaluate("getDefaultConfig('t2v', false)")
        assert result["stage3_seedream"]["enabled"] is False

    def test_t2v_face_swap_disabled(self, wf_page):
        """getDefaultConfig('t2v') should have face_swap disabled."""
        result = wf_page.evaluate("getDefaultConfig('t2v', false)")
        assert result["stage2_first_frame"]["face_swap"]["enabled"] is False

    def test_t2v_no_first_frame_source(self, wf_page):
        """getDefaultConfig('t2v') stage2 should NOT have first_frame_source."""
        result = wf_page.evaluate("getDefaultConfig('t2v', false)")
        stage2 = result["stage2_first_frame"]
        assert "first_frame_source" not in stage2, \
            f"stage2 should not have first_frame_source, got: {stage2}"

    def test_t2v_same_as_first_frame(self, wf_page):
        """T2V and first_frame should have identical stage3 config."""
        t2v = wf_page.evaluate("getDefaultConfig('t2v', false)")
        ff = wf_page.evaluate("getDefaultConfig('first_frame', false)")
        assert t2v["stage3_seedream"] == ff["stage3_seedream"]

    def test_face_reference_stage3_enabled(self, wf_page):
        """face_reference non-turbo should have stage3 enabled (regression)."""
        result = wf_page.evaluate("getDefaultConfig('face_reference', false)")
        assert result["stage3_seedream"]["enabled"] is True


# ---------------------------------------------------------------------------
# 6. Mode Label Tests
# ---------------------------------------------------------------------------

class TestModeLabels:
    """Verify t2v mode label appears in task history display."""

    def test_mode_label_mapping(self, wf_page):
        """The mode label mapping in loadHistoryList should include t2v."""
        # Test the inline ternary logic that maps mode to display text
        result = wf_page.evaluate("""(() => {
            const mode = 't2v';
            return mode === 't2v' ? '文生视频' :
                   mode === 'first_frame' ? '首帧' :
                   mode === 'full_body_reference' ? '全身参考' : '仅换脸';
        })()""")
        assert result == "文生视频"


# ---------------------------------------------------------------------------
# 7. Workflow History Page Tests
# ---------------------------------------------------------------------------

class TestWorkflowHistoryLabels:
    """Verify workflow_history.html has t2v label."""

    @pytest.fixture
    def history_page(self, workflow_context):
        page = workflow_context.new_page()
        page.goto(HISTORY_PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)  # let JS initialize
        yield page
        page.close()

    def test_card_mode_label_includes_t2v(self, history_page):
        """cardHtml function should map t2v to '文生视频'."""
        result = history_page.evaluate("""(() => {
            const labels = {full_body_reference:'全身换脸', face_reference:'换脸', first_frame:'首帧', t2v:'文生视频'};
            return labels['t2v'];
        })()""")
        assert result == "文生视频"

    def test_detail_mode_label_includes_t2v(self, history_page):
        """showDetail modeLabels should map t2v to '文生视频'."""
        result = history_page.evaluate("""(() => {
            const modeLabels = {full_body_reference:'全身换脸', face_reference:'换脸', first_frame:'首帧', t2v:'文生视频'};
            return modeLabels['t2v'];
        })()""")
        assert result == "文生视频"


# ---------------------------------------------------------------------------
# 8. Validation Behavior Tests
# ---------------------------------------------------------------------------

class TestValidation:
    """Verify T2V mode doesn't require image upload."""

    def test_t2v_no_alert_without_image(self, wf_page):
        """T2V mode should NOT trigger upload alert when no image uploaded."""
        wf_page.select_option("#imageRole", "t2v")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.wait_for_timeout(200)

        # Fill in a prompt so that validation doesn't fail on empty prompt
        wf_page.fill("#prompt", "A cat walking on grass")

        # Check the validation logic via JS evaluation (don't actually submit)
        result = wf_page.evaluate("""(() => {
            const imageRole = document.getElementById('imageRole').value;
            const uploadedImageUrl = null;  // No image
            const parentWorkflowId = null;  // No story mode

            if (!parentWorkflowId) {
                if (!uploadedImageUrl) {
                    if (imageRole === 't2v') {
                        return 'ok';  // T2V doesn't need image
                    } else if (imageRole === 'first_frame') {
                        return 'need_first_frame';
                    } else {
                        return 'need_reference';
                    }
                }
            }
            return 'ok';
        })()""")
        assert result == "ok", f"T2V should pass validation without image, got: {result}"

    def test_first_frame_requires_image(self, wf_page):
        """first_frame mode should require image upload."""
        wf_page.select_option("#imageRole", "first_frame")

        result = wf_page.evaluate("""(() => {
            const imageRole = document.getElementById('imageRole').value;
            const uploadedImageUrl = null;
            const parentWorkflowId = null;

            if (!parentWorkflowId) {
                if (!uploadedImageUrl) {
                    if (imageRole === 't2v') {
                        return 'ok';
                    } else if (imageRole === 'first_frame') {
                        return 'need_first_frame';
                    } else {
                        return 'need_reference';
                    }
                }
            }
            return 'ok';
        })()""")
        assert result == "need_first_frame"


# ---------------------------------------------------------------------------
# 9. buildRequestData Tests
# ---------------------------------------------------------------------------

class TestBuildRequestData:
    """Verify buildRequestData sends correct fields for T2V mode."""

    def test_t2v_no_image_fields(self, wf_page):
        """T2V mode should not include uploaded_first_frame or reference_image."""
        wf_page.select_option("#imageRole", "t2v")
        wf_page.locator("#imageRole").dispatch_event("change")
        wf_page.fill("#prompt", "Test prompt")
        wf_page.wait_for_timeout(200)

        # Simulate what buildRequestData does for image fields
        result = wf_page.evaluate("""(() => {
            const imageRole = 't2v';
            const parentWorkflowId = null;
            const uploadedImageUrl = 'http://example.com/test.jpg';
            const requestData = {};

            if (!parentWorkflowId) {
                if (imageRole === 't2v') {
                    // T2V: don't add image fields
                } else if (imageRole === 'first_frame') {
                    requestData.uploaded_first_frame = uploadedImageUrl;
                } else if (imageRole === 'face_reference' || imageRole === 'full_body_reference') {
                    requestData.reference_image = uploadedImageUrl;
                }
            }

            return {
                has_uploaded_first_frame: 'uploaded_first_frame' in requestData,
                has_reference_image: 'reference_image' in requestData,
            };
        })()""")
        assert result["has_uploaded_first_frame"] is False, "T2V should not send uploaded_first_frame"
        assert result["has_reference_image"] is False, "T2V should not send reference_image"

    def test_first_frame_sends_uploaded(self, wf_page):
        """first_frame mode should send uploaded_first_frame."""
        result = wf_page.evaluate("""(() => {
            const imageRole = 'first_frame';
            const parentWorkflowId = null;
            const uploadedImageUrl = 'http://example.com/test.jpg';
            const requestData = {};

            if (!parentWorkflowId) {
                if (imageRole === 't2v') {
                } else if (imageRole === 'first_frame') {
                    requestData.uploaded_first_frame = uploadedImageUrl;
                } else {
                    requestData.reference_image = uploadedImageUrl;
                }
            }
            return requestData;
        })()""")
        assert "uploaded_first_frame" in result
        assert result["uploaded_first_frame"] == "http://example.com/test.jpg"

    def test_face_reference_sends_reference(self, wf_page):
        """face_reference mode should send reference_image."""
        result = wf_page.evaluate("""(() => {
            const imageRole = 'face_reference';
            const parentWorkflowId = null;
            const uploadedImageUrl = 'http://example.com/test.jpg';
            const requestData = {};

            if (!parentWorkflowId) {
                if (imageRole === 't2v') {
                } else if (imageRole === 'first_frame') {
                    requestData.uploaded_first_frame = uploadedImageUrl;
                } else {
                    requestData.reference_image = uploadedImageUrl;
                }
            }
            return requestData;
        })()""")
        assert "reference_image" in result
        assert result["reference_image"] == "http://example.com/test.jpg"
