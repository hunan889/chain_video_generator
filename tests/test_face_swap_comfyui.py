"""
Unit tests for ComfyUI-based face swap migration.

Tests:
1. build_face_swap_workflow() — workflow structure
2. ComfyUIClient.get_output_images() — image output parsing
3. TaskManager.find_available_client() — idle worker selection + polling
4. _apply_face_swap_via_comfyui() — end-to-end with mocks
"""
import asyncio
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ─── 1. build_face_swap_workflow ─────────────────────────────────────────────

class TestBuildFaceSwapWorkflow:

    def test_structure(self):
        from api.services.workflow_builder import build_face_swap_workflow
        wf = build_face_swap_workflow("frame.png", "face.png", strength=0.8)

        # Must have exactly 4 nodes
        assert len(wf) == 4

        # Node types
        assert wf["1"]["class_type"] == "LoadImage"
        assert wf["2"]["class_type"] == "LoadImage"
        assert wf["3"]["class_type"] == "ReActorFaceSwap"
        assert wf["4"]["class_type"] == "SaveImage"

    def test_wiring(self):
        from api.services.workflow_builder import build_face_swap_workflow
        wf = build_face_swap_workflow("target.png", "source.png")

        # LoadImage filenames
        assert wf["1"]["inputs"]["image"] == "target.png"
        assert wf["2"]["inputs"]["image"] == "source.png"

        # ReActor input_image wired to node 1, source_image wired to node 2
        assert wf["3"]["inputs"]["input_image"] == ["1", 0]
        assert wf["3"]["inputs"]["source_image"] == ["2", 0]

        # SaveImage wired to ReActor output
        assert wf["4"]["inputs"]["images"] == ["3", 0]

    def test_reactor_params_match_inject_reactor(self):
        """Verify ReActor params are consistent with _inject_reactor()."""
        from api.services.workflow_builder import build_face_swap_workflow
        wf = build_face_swap_workflow("a.png", "b.png")
        r = wf["3"]["inputs"]

        assert r["swap_model"] == "inswapper_128.onnx"
        assert r["facedetection"] == "retinaface_resnet50"
        assert r["face_restore_model"] == "codeformer-v0.1.0.pth"
        assert r["face_restore_visibility"] == 0.85
        assert r["codeformer_weight"] == 0.3


# ─── 2. ComfyUIClient.get_output_images ─────────────────────────────────────

class TestGetOutputImages:

    @pytest.mark.asyncio
    async def test_extracts_images(self):
        from api.services.comfyui_client import ComfyUIClient
        client = ComfyUIClient("http://fake:8188")
        client.get_history = AsyncMock(return_value={
            "outputs": {
                "4": {
                    "images": [
                        {"filename": "face_swap_00001.png", "subfolder": "", "type": "output"},
                        {"filename": "face_swap_00001.png", "subfolder": "", "type": "temp"},
                    ]
                }
            }
        })
        images = await client.get_output_images("test-prompt-id")
        # Should only return type=output
        assert len(images) == 1
        assert images[0]["filename"] == "face_swap_00001.png"
        assert images[0]["type"] == "output"

    @pytest.mark.asyncio
    async def test_empty_on_no_history(self):
        from api.services.comfyui_client import ComfyUIClient
        client = ComfyUIClient("http://fake:8188")
        client.get_history = AsyncMock(return_value=None)
        assert await client.get_output_images("missing") == []

    @pytest.mark.asyncio
    async def test_empty_on_no_images_key(self):
        from api.services.comfyui_client import ComfyUIClient
        client = ComfyUIClient("http://fake:8188")
        client.get_history = AsyncMock(return_value={
            "outputs": {
                "10": {"gifs": [{"filename": "video.gif", "type": "output"}]}
            }
        })
        assert await client.get_output_images("video-prompt") == []


# ─── 3. TaskManager.find_available_client ────────────────────────────────────

class TestFindAvailableClient:

    def _make_task_manager(self, workers: dict, busy_urls: list[str] = None):
        """Create a TaskManager with mocked workers and Redis."""
        from api.services.task_manager import TaskManager
        tm = TaskManager()
        tm._workers = workers
        # Mock get_running_tasks_by_worker
        busy = {url: {"task_id": "t"} for url in (busy_urls or [])}
        tm.get_running_tasks_by_worker = AsyncMock(return_value=busy)
        return tm

    @pytest.mark.asyncio
    async def test_returns_idle_worker(self):
        client_a = MagicMock()
        client_a.base_url = "http://gpu1:8188"
        workers = {
            "a14b:8188": {"model_key": "a14b", "url": "http://gpu1:8188", "client": client_a}
        }
        tm = self._make_task_manager(workers, busy_urls=[])
        result = await tm.find_available_client(timeout=5)
        assert result is client_a
        # Should be marked as direct-busy now
        assert "http://gpu1:8188" in tm._direct_busy_urls
        # Release
        tm.release_client(client_a)
        assert "http://gpu1:8188" not in tm._direct_busy_urls

    @pytest.mark.asyncio
    async def test_returns_none_when_all_busy_and_timeout(self):
        client_a = MagicMock()
        client_a.base_url = "http://gpu1:8188"
        workers = {
            "a14b:8188": {"model_key": "a14b", "url": "http://gpu1:8188", "client": client_a}
        }
        tm = self._make_task_manager(workers, busy_urls=["http://gpu1:8188"])
        result = await tm.find_available_client(timeout=3)
        assert result is None

    @pytest.mark.asyncio
    async def test_prefers_matching_model_key(self):
        client_a = MagicMock()
        client_a.base_url = "http://gpu1:8188"
        client_b = MagicMock()
        client_b.base_url = "http://gpu2:8189"
        workers = {
            "a14b:8188": {"model_key": "a14b", "url": "http://gpu1:8188", "client": client_a},
            "5b:8189": {"model_key": "5b", "url": "http://gpu2:8189", "client": client_b},
        }
        tm = self._make_task_manager(workers, busy_urls=[])
        result = await tm.find_available_client(prefer_model_key="5b", timeout=5)
        assert result is client_b

    @pytest.mark.asyncio
    async def test_falls_back_to_any_idle(self):
        """When preferred model_key has no idle workers, pick any idle worker."""
        client_a = MagicMock()
        client_a.base_url = "http://gpu1:8188"
        client_b = MagicMock()
        client_b.base_url = "http://gpu2:8189"
        workers = {
            "a14b:8188": {"model_key": "a14b", "url": "http://gpu1:8188", "client": client_a},
            "5b:8189": {"model_key": "5b", "url": "http://gpu2:8189", "client": client_b},
        }
        # 5b is busy, a14b is idle
        tm = self._make_task_manager(workers, busy_urls=["http://gpu2:8189"])
        result = await tm.find_available_client(prefer_model_key="5b", timeout=5)
        assert result is client_a

    @pytest.mark.asyncio
    async def test_concurrent_calls_get_different_workers(self):
        """Concurrent find_available_client calls should spread across workers."""
        client_a = MagicMock()
        client_a.base_url = "http://gpu1:8188"
        client_b = MagicMock()
        client_b.base_url = "http://gpu2:8189"
        client_c = MagicMock()
        client_c.base_url = "http://gpu3:8190"
        workers = {
            "a14b:8188": {"model_key": "a14b", "url": "http://gpu1:8188", "client": client_a},
            "a14b:8189": {"model_key": "a14b", "url": "http://gpu2:8189", "client": client_b},
            "a14b:8190": {"model_key": "a14b", "url": "http://gpu3:8190", "client": client_c},
        }
        tm = self._make_task_manager(workers, busy_urls=[])
        # Get 3 workers concurrently — each should be different
        r1 = await tm.find_available_client(timeout=5)
        r2 = await tm.find_available_client(timeout=5)
        r3 = await tm.find_available_client(timeout=5)
        assigned = {r1.base_url, r2.base_url, r3.base_url}
        assert len(assigned) == 3, f"Expected 3 different workers, got {assigned}"
        # Release all
        tm.release_client(r1)
        tm.release_client(r2)
        tm.release_client(r3)
        assert len(tm._direct_busy_urls) == 0

    @pytest.mark.asyncio
    async def test_direct_busy_blocks_second_call(self):
        """After first call marks worker busy, second call with only 1 worker should timeout."""
        client_a = MagicMock()
        client_a.base_url = "http://gpu1:8188"
        workers = {
            "a14b:8188": {"model_key": "a14b", "url": "http://gpu1:8188", "client": client_a}
        }
        tm = self._make_task_manager(workers, busy_urls=[])
        r1 = await tm.find_available_client(timeout=5)
        assert r1 is client_a
        # Second call should timeout (worker is direct-busy)
        r2 = await tm.find_available_client(timeout=3)
        assert r2 is None
        # Release and retry
        tm.release_client(r1)
        r3 = await tm.find_available_client(timeout=5)
        assert r3 is client_a

    @pytest.mark.asyncio
    async def test_waits_then_returns_when_worker_frees(self):
        """Simulate worker becoming idle after 2 polls."""
        client_a = MagicMock()
        client_a.base_url = "http://gpu1:8188"
        workers = {
            "a14b:8188": {"model_key": "a14b", "url": "http://gpu1:8188", "client": client_a}
        }
        from api.services.task_manager import TaskManager
        tm = TaskManager()
        tm._workers = workers

        # First 2 calls: busy. Third call: idle.
        call_count = 0
        async def mock_running_tasks():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {"http://gpu1:8188": {"task_id": "t1"}}
            return {}

        tm.get_running_tasks_by_worker = mock_running_tasks
        result = await tm.find_available_client(timeout=30)
        assert result is client_a
        assert call_count == 3  # polled 3 times


# ─── 4. _apply_face_swap_via_comfyui (end-to-end with mocks) ────────────────

class TestApplyFaceSwapViaComfyui:

    @pytest.mark.asyncio
    async def test_success_flow(self):
        """Full success path with all dependencies mocked."""
        from api.routes.workflow_executor import _apply_face_swap_via_comfyui

        # Mock task_manager
        mock_client = AsyncMock()
        mock_client.base_url = "http://gpu1:8188"
        mock_client.upload_image = AsyncMock(return_value={"name": "test.png"})
        mock_client.queue_prompt = AsyncMock(return_value="prompt-123")
        mock_client.wait_for_completion = AsyncMock(return_value={})
        mock_client.get_output_images = AsyncMock(return_value=[
            {"filename": "face_swap_00001_.png", "subfolder": "", "type": "output"}
        ])
        mock_client.download_file = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfakedata")

        mock_tm = AsyncMock()
        mock_tm.find_available_client = AsyncMock(return_value=mock_client)

        # Create a 1x1 red pixel PNG for frame download
        fake_png = b"\x89PNG\r\n\x1a\nfakepng"
        face_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfakeface").decode()

        with patch("api.routes.workflow_executor.aiohttp.ClientSession") as mock_session_cls, \
             patch("api.services.storage.save_upload", new_callable=AsyncMock) as mock_save:

            # Mock aiohttp download
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.read = AsyncMock(return_value=fake_png)
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_get = MagicMock()
            mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_get.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_get)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            mock_save.return_value = ("/path/face_swap_abc.png", "http://host/uploads/face_swap_abc.png")

            result = await _apply_face_swap_via_comfyui(
                frame_url="http://localhost:8000/api/v1/results/frame.png",
                reference_face=face_b64,
                strength=1.0,
                task_manager=mock_tm,
            )

        assert result == "http://host/uploads/face_swap_abc.png"
        # Verify workflow was submitted
        mock_client.queue_prompt.assert_called_once()
        mock_client.wait_for_completion.assert_called_once_with("prompt-123", timeout=120)
        # Verify 2 images uploaded (frame + face)
        assert mock_client.upload_image.call_count == 2
        # Verify release_client was called (worker freed)
        mock_tm.release_client.assert_called_once_with(mock_client)

    @pytest.mark.asyncio
    async def test_returns_none_on_no_idle_worker(self):
        from api.routes.workflow_executor import _apply_face_swap_via_comfyui

        mock_tm = AsyncMock()
        mock_tm.find_available_client = AsyncMock(return_value=None)

        fake_png = b"\x89PNG\r\n\x1a\nfakepng"
        face_b64 = base64.b64encode(b"fakeface").decode()

        with patch("api.routes.workflow_executor.aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.read = AsyncMock(return_value=fake_png)
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_get = MagicMock()
            mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_get.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_get)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            result = await _apply_face_swap_via_comfyui(
                frame_url="http://localhost:8000/frame.png",
                reference_face=face_b64,
                task_manager=mock_tm,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_no_task_manager(self):
        from api.routes.workflow_executor import _apply_face_swap_via_comfyui

        face_b64 = base64.b64encode(b"fakeface").decode()
        fake_png = b"\x89PNG\r\n\x1a\nfakepng"

        with patch("api.routes.workflow_executor.aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.read = AsyncMock(return_value=fake_png)
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_get = MagicMock()
            mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_get.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_get)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            result = await _apply_face_swap_via_comfyui(
                frame_url="http://localhost:8000/frame.png",
                reference_face=face_b64,
                task_manager=None,
            )

        assert result is None
