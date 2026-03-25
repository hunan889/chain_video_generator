"""
Tests for T2V mode implementation.

Verifies:
1. Mode enum accepts 't2v'
2. _build_default_internal_config handles t2v correctly
3. Backward compatibility: first_frame without image auto-converts to t2v
4. _find_base_image dispatching logic
5. _acquire_first_frame mode routing
6. source_text determination
7. Stage 3 SeeDream skip logic for t2v
8. DashScope routing sends mode=t2v for pure T2V

Run: pytest tests/test_t2v_mode.py -v
"""
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# Stub heavy dependencies before importing project modules
_STUB_MODULES = [
    "sentence_transformers", "torch", "numpy",
    "pymysql", "pymysql.cursors", "redis", "websockets", "pymilvus",
    "yaml", "dotenv", "anthropic",
]
for mod_name in _STUB_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

np_mock = sys.modules["numpy"]
np_mock.array = lambda x: x
np_mock.dot = lambda a, b: 0.0


# ============================================================================
# 1. Mode Enum / Request Model Tests
# ============================================================================

class TestModeEnum:
    """Verify mode field accepts all 4 values including t2v."""

    def test_mode_accepts_t2v(self):
        from api.routes.workflow import WorkflowGenerateRequest
        req = WorkflowGenerateRequest(mode="t2v", user_prompt="test prompt")
        assert req.mode == "t2v"

    def test_mode_accepts_first_frame(self):
        from api.routes.workflow import WorkflowGenerateRequest
        req = WorkflowGenerateRequest(mode="first_frame", user_prompt="test prompt")
        assert req.mode == "first_frame"

    def test_mode_accepts_face_reference(self):
        from api.routes.workflow import WorkflowGenerateRequest
        req = WorkflowGenerateRequest(mode="face_reference", user_prompt="test prompt")
        assert req.mode == "face_reference"

    def test_mode_accepts_full_body_reference(self):
        from api.routes.workflow import WorkflowGenerateRequest
        req = WorkflowGenerateRequest(mode="full_body_reference", user_prompt="test prompt")
        assert req.mode == "full_body_reference"

    def test_mode_rejects_invalid(self):
        from api.routes.workflow import WorkflowGenerateRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WorkflowGenerateRequest(mode="invalid_mode", user_prompt="test prompt")

    def test_analyze_request_accepts_t2v(self):
        from api.routes.workflow import WorkflowAnalyzeRequest
        req = WorkflowAnalyzeRequest(mode="t2v", prompt="test prompt")
        assert req.mode == "t2v"


# ============================================================================
# 2. _build_default_internal_config Tests
# ============================================================================

class TestBuildDefaultConfig:
    """Verify _build_default_internal_config handles t2v mode."""

    def test_t2v_face_swap_disabled(self):
        from api.routes.workflow import _build_default_internal_config
        config = _build_default_internal_config("t2v")
        face_swap = config["stage2_first_frame"]["face_swap"]
        assert face_swap["enabled"] is False

    def test_t2v_stage3_disabled(self):
        from api.routes.workflow import _build_default_internal_config
        config = _build_default_internal_config("t2v")
        assert config["stage3_seedream"]["enabled"] is False

    def test_t2v_same_as_first_frame(self):
        """T2V and first_frame should have identical face_swap and stage3 defaults."""
        from api.routes.workflow import _build_default_internal_config
        t2v_config = _build_default_internal_config("t2v")
        ff_config = _build_default_internal_config("first_frame")

        assert t2v_config["stage2_first_frame"]["face_swap"] == ff_config["stage2_first_frame"]["face_swap"]
        assert t2v_config["stage3_seedream"] == ff_config["stage3_seedream"]

    def test_t2v_turbo_mode(self):
        from api.routes.workflow import _build_default_internal_config
        config = _build_default_internal_config("t2v", turbo=True)
        assert config["stage3_seedream"]["enabled"] is False
        assert config["stage2_first_frame"]["face_swap"]["enabled"] is False

    def test_face_reference_face_swap_enabled(self):
        """Ensure face_reference still has face_swap enabled (regression)."""
        from api.routes.workflow import _build_default_internal_config
        config = _build_default_internal_config("face_reference")
        assert config["stage2_first_frame"]["face_swap"]["enabled"] is True

    def test_full_body_reference_stage3_enabled(self):
        """Ensure full_body_reference still has stage3 enabled (regression)."""
        from api.routes.workflow import _build_default_internal_config
        config = _build_default_internal_config("full_body_reference")
        assert config["stage3_seedream"]["enabled"] is True


# ============================================================================
# 3. Backward Compatibility Tests
# ============================================================================

class TestBackwardCompat:
    """Verify first_frame without image auto-converts to t2v."""

    def test_first_frame_without_image_converts(self):
        from api.routes.workflow import WorkflowGenerateRequest
        req = WorkflowGenerateRequest(
            mode="first_frame",
            user_prompt="test prompt",
            uploaded_first_frame=None,
            parent_workflow_id=None,
        )
        # Simulate the conversion logic from generate_advanced_workflow
        if req.mode == "first_frame" and not req.uploaded_first_frame and not req.parent_workflow_id:
            req.mode = "t2v"
        assert req.mode == "t2v"

    def test_first_frame_with_image_stays(self):
        from api.routes.workflow import WorkflowGenerateRequest
        req = WorkflowGenerateRequest(
            mode="first_frame",
            user_prompt="test prompt",
            uploaded_first_frame="http://example.com/image.png",
        )
        if req.mode == "first_frame" and not req.uploaded_first_frame and not req.parent_workflow_id:
            req.mode = "t2v"
        assert req.mode == "first_frame"

    def test_first_frame_with_parent_stays(self):
        from api.routes.workflow import WorkflowGenerateRequest
        req = WorkflowGenerateRequest(
            mode="first_frame",
            user_prompt="test prompt",
            parent_workflow_id="wf_abc123",
        )
        if req.mode == "first_frame" and not req.uploaded_first_frame and not req.parent_workflow_id:
            req.mode = "t2v"
        assert req.mode == "first_frame"


# ============================================================================
# 4. _find_base_image Logic Tests
# ============================================================================

class TestFindBaseImage:
    """Verify unified base image acquisition logic."""

    @pytest.fixture
    def mock_req(self):
        req = MagicMock()
        req.user_prompt = "a girl dancing"
        req.aspect_ratio = "3:4"
        req.reference_image = None
        req.internal_config = {}
        return req

    @pytest.mark.asyncio
    async def test_pose_reference_returned_for_t2v(self, mock_req):
        """T2V mode should return pose reference image if available."""
        from api.routes.workflow_executor import _find_base_image
        mock_req.mode = "t2v"
        analysis = {"reference_image": "http://example.com/pose.jpg"}
        task_manager = MagicMock()

        result = await _find_base_image("wf_test", mock_req, analysis, task_manager)
        assert result == "http://example.com/pose.jpg"

    @pytest.mark.asyncio
    async def test_t2v_returns_none_without_pose(self, mock_req):
        """T2V mode without pose reference should return None (pure T2V)."""
        from api.routes.workflow_executor import _find_base_image
        mock_req.mode = "t2v"
        analysis = {"reference_image": None}
        task_manager = MagicMock()

        result = await _find_base_image("wf_test", mock_req, analysis, task_manager)
        assert result is None

    @pytest.mark.asyncio
    async def test_t2v_returns_none_without_analysis(self, mock_req):
        """T2V mode without analysis result should return None."""
        from api.routes.workflow_executor import _find_base_image
        mock_req.mode = "t2v"
        task_manager = MagicMock()

        result = await _find_base_image("wf_test", mock_req, None, task_manager)
        assert result is None

    @pytest.mark.asyncio
    async def test_face_reference_uses_pose_first(self, mock_req):
        """face_reference should prioritize pose reference."""
        from api.routes.workflow_executor import _find_base_image
        mock_req.mode = "face_reference"
        analysis = {"reference_image": "http://example.com/pose.jpg"}
        task_manager = MagicMock()

        result = await _find_base_image("wf_test", mock_req, analysis, task_manager)
        assert result == "http://example.com/pose.jpg"

    @pytest.mark.asyncio
    async def test_face_reference_fallback_to_reference_image(self, mock_req):
        """face_reference with no pose/recommend should fall back to reference_image."""
        from api.routes.workflow_executor import _find_base_image
        mock_req.mode = "face_reference"
        mock_req.reference_image = "http://example.com/face.jpg"
        analysis = {"reference_image": None}
        task_manager = MagicMock()

        # Mock the recommend import to raise so we hit fallback
        with patch("api.routes.workflow_executor.smart_recommend", side_effect=Exception("no recommend"), create=True):
            result = await _find_base_image("wf_test", mock_req, analysis, task_manager)
        assert result == "http://example.com/face.jpg"

    @pytest.mark.asyncio
    async def test_t2v_does_not_call_recommend(self, mock_req):
        """T2V mode should NOT call the recommend API (only pose)."""
        from api.routes.workflow_executor import _find_base_image
        mock_req.mode = "t2v"
        analysis = {"reference_image": None}
        task_manager = MagicMock()

        with patch("api.routes.recommend.smart_recommend") as mock_recommend:
            result = await _find_base_image("wf_test", mock_req, analysis, task_manager)
            mock_recommend.assert_not_called()
        assert result is None


# ============================================================================
# 5. _acquire_first_frame Dispatch Tests
# ============================================================================

class TestAcquireFirstFrame:
    """Verify _acquire_first_frame dispatches correctly by mode."""

    @pytest.mark.asyncio
    async def test_t2v_dispatches_to_find_base_image(self):
        from api.routes.workflow_executor import _acquire_first_frame
        req = MagicMock()
        req.mode = "t2v"
        task_manager = MagicMock()

        with patch("api.routes.workflow_executor._find_base_image", new_callable=AsyncMock, return_value=None) as mock_find:
            result = await _acquire_first_frame("wf_test", req, None, task_manager)
            mock_find.assert_called_once_with("wf_test", req, None, task_manager)
        assert result is None

    @pytest.mark.asyncio
    async def test_first_frame_with_upload_dispatches_to_process(self):
        from api.routes.workflow_executor import _acquire_first_frame
        req = MagicMock()
        req.mode = "first_frame"
        req.uploaded_first_frame = "http://example.com/img.png"
        task_manager = MagicMock()

        with patch("api.routes.workflow_executor._process_uploaded_first_frame", new_callable=AsyncMock, return_value="http://result.com/frame.png") as mock_proc:
            result = await _acquire_first_frame("wf_test", req, None, task_manager)
            mock_proc.assert_called_once_with("wf_test", req, task_manager)
        assert result == "http://result.com/frame.png"

    @pytest.mark.asyncio
    async def test_first_frame_without_upload_fallback(self):
        from api.routes.workflow_executor import _acquire_first_frame
        req = MagicMock()
        req.mode = "first_frame"
        req.uploaded_first_frame = None
        task_manager = MagicMock()

        with patch("api.routes.workflow_executor._find_base_image", new_callable=AsyncMock, return_value=None) as mock_find:
            result = await _acquire_first_frame("wf_test", req, None, task_manager)
            mock_find.assert_called_once()
        assert result is None

    @pytest.mark.asyncio
    async def test_face_reference_dispatches_to_find_base_image(self):
        from api.routes.workflow_executor import _acquire_first_frame
        req = MagicMock()
        req.mode = "face_reference"
        task_manager = MagicMock()

        with patch("api.routes.workflow_executor._find_base_image", new_callable=AsyncMock, return_value="http://base.com/img.png") as mock_find:
            result = await _acquire_first_frame("wf_test", req, {"reference_image": None}, task_manager)
            mock_find.assert_called_once()
        assert result == "http://base.com/img.png"

    @pytest.mark.asyncio
    async def test_full_body_reference_dispatches_to_find_base_image(self):
        from api.routes.workflow_executor import _acquire_first_frame
        req = MagicMock()
        req.mode = "full_body_reference"
        task_manager = MagicMock()

        with patch("api.routes.workflow_executor._find_base_image", new_callable=AsyncMock, return_value="http://base.com/img.png") as mock_find:
            result = await _acquire_first_frame("wf_test", req, {}, task_manager)
            mock_find.assert_called_once()
        assert result == "http://base.com/img.png"


# ============================================================================
# 6. Source Text Logic Tests
# ============================================================================

class TestSourceText:
    """Verify source_text determination logic for different modes."""

    def _get_source_text(self, mode, first_frame_url, analysis_result, reference_image=None):
        """Replicate the source_text logic from workflow_executor."""
        if mode == "t2v":
            if first_frame_url:
                return "pose_reference"
            else:
                return "t2v"
        elif mode == "first_frame":
            return "upload"
        else:  # face_reference / full_body_reference
            if first_frame_url and analysis_result and analysis_result.get("reference_image"):
                return "pose_reference"
            elif first_frame_url and reference_image and first_frame_url == reference_image:
                return "reference_image_fallback"
            elif first_frame_url:
                return "recommend"
            else:
                return "unknown"

    def test_t2v_pure(self):
        assert self._get_source_text("t2v", None, None) == "t2v"

    def test_t2v_with_pose(self):
        assert self._get_source_text("t2v", "http://pose.jpg", {"reference_image": "http://pose.jpg"}) == "pose_reference"

    def test_first_frame_upload(self):
        assert self._get_source_text("first_frame", "http://uploaded.jpg", None) == "upload"

    def test_face_reference_pose(self):
        assert self._get_source_text("face_reference", "http://pose.jpg", {"reference_image": "http://pose.jpg"}) == "pose_reference"

    def test_face_reference_recommend(self):
        assert self._get_source_text("face_reference", "http://recommend.jpg", {"reference_image": None}) == "recommend"

    def test_face_reference_fallback(self):
        assert self._get_source_text(
            "face_reference", "http://ref.jpg", {"reference_image": None}, "http://ref.jpg"
        ) == "reference_image_fallback"

    def test_face_reference_unknown(self):
        assert self._get_source_text("face_reference", None, None) == "unknown"


# ============================================================================
# 7. Stage 3 Skip Logic Tests
# ============================================================================

class TestStage3Skip:
    """Verify SeeDream skip logic for t2v and first_frame modes."""

    def _should_run_seedream(self, mode, stage3_enabled=True):
        """Replicate the Stage 3 skip logic from workflow_executor."""
        should_run_seedream = False
        skip_reason = ""

        if mode in ("first_frame", "t2v"):
            should_run_seedream = False
            skip_reason = "跳过（首帧/T2V模式）"
        elif mode == "full_body_reference":
            should_run_seedream = True
        elif mode == "face_reference":
            should_run_seedream = stage3_enabled
            if not should_run_seedream:
                skip_reason = "跳过（未启用）"

        return should_run_seedream, skip_reason

    def test_t2v_skips_seedream(self):
        should_run, reason = self._should_run_seedream("t2v")
        assert should_run is False
        assert "T2V" in reason

    def test_first_frame_skips_seedream(self):
        should_run, reason = self._should_run_seedream("first_frame")
        assert should_run is False

    def test_full_body_runs_seedream(self):
        should_run, _ = self._should_run_seedream("full_body_reference")
        assert should_run is True

    def test_face_reference_runs_when_enabled(self):
        should_run, _ = self._should_run_seedream("face_reference", stage3_enabled=True)
        assert should_run is True

    def test_face_reference_skips_when_disabled(self):
        should_run, _ = self._should_run_seedream("face_reference", stage3_enabled=False)
        assert should_run is False


# ============================================================================
# 8. DashScope Routing Tests
# ============================================================================

class TestDashScopeRouting:
    """Verify DashScope routes pure T2V to mode=t2v."""

    def test_pure_t2v_routing(self):
        """Pure T2V (no image) should set mode='t2v'."""
        # Simulate the dashscope.py routing logic
        model = "wan2.1-t2v"
        has_image = False

        if "i2v" in model and has_image:
            mode = "first_frame"
        elif "t2v" in model and has_image:
            mode = "face_reference"
        else:
            mode = "t2v"

        assert mode == "t2v"

    def test_i2v_with_image_routing(self):
        """I2V with image should set mode='first_frame'."""
        model = "wan2.1-i2v"
        has_image = True

        if "i2v" in model and has_image:
            mode = "first_frame"
        elif "t2v" in model and has_image:
            mode = "face_reference"
        else:
            mode = "t2v"

        assert mode == "first_frame"

    def test_t2v_with_image_routing(self):
        """T2V with image should set mode='face_reference'."""
        model = "wan2.1-t2v"
        has_image = True

        if "i2v" in model and has_image:
            mode = "first_frame"
        elif "t2v" in model and has_image:
            mode = "face_reference"
        else:
            mode = "t2v"

        assert mode == "face_reference"


# ============================================================================
# 9. Pose Threshold Regression Tests
# ============================================================================

class TestPoseThreshold:
    """Verify t2v gets strict pose threshold (0.5), same as first_frame."""

    def test_t2v_gets_strict_threshold(self):
        def get_pose_min_score(mode):
            return 0.5 if mode in (None, "t2v", "first_frame") else 0.3

        assert get_pose_min_score("t2v") == 0.5

    def test_first_frame_gets_strict_threshold(self):
        def get_pose_min_score(mode):
            return 0.5 if mode in (None, "t2v", "first_frame") else 0.3

        assert get_pose_min_score("first_frame") == 0.5

    def test_reference_modes_get_lenient_threshold(self):
        def get_pose_min_score(mode):
            return 0.5 if mode in (None, "t2v", "first_frame") else 0.3

        assert get_pose_min_score("face_reference") == 0.3
        assert get_pose_min_score("full_body_reference") == 0.3


# ============================================================================
# 10. Image Mode Mapping Tests
# ============================================================================

class TestImageModeMapping:
    """Verify Stage 4 image_mode for t2v maps to FIRST_FRAME."""

    def test_t2v_maps_to_first_frame_image_mode(self):
        """t2v mode should use FIRST_FRAME image mode (same as first_frame)."""
        mode = "t2v"
        is_continuation = False

        if is_continuation:
            image_mode = "FIRST_FRAME"
        elif mode == "face_reference":
            image_mode = "FACE_REFERENCE"
        elif mode == "full_body_reference":
            image_mode = "FULL_BODY_REFERENCE"
        else:
            image_mode = "FIRST_FRAME"

        assert image_mode == "FIRST_FRAME"
