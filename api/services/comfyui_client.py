import aiohttp
import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ComfyUIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def is_alive(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/system_stats", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def queue_prompt(self, workflow: dict) -> str:
        session = await self._get_session()
        payload = {"prompt": workflow}
        async with session.post(f"{self.base_url}/prompt", json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"ComfyUI prompt failed ({resp.status}): {text}")
            data = await resp.json()
            return data["prompt_id"]

    async def get_history(self, prompt_id: str) -> Optional[dict]:
        session = await self._get_session()
        async with session.get(f"{self.base_url}/history/{prompt_id}") as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get(prompt_id)
    async def get_output_files(self, prompt_id: str) -> list[dict]:
        history = await self.get_history(prompt_id)
        if not history:
            return []
        outputs = history.get("outputs", {})
        files = []
        for node_id, node_output in outputs.items():
            if "gifs" in node_output:
                for f in node_output["gifs"]:
                    files.append(f)
            elif "videos" in node_output:
                for f in node_output["videos"]:
                    files.append(f)
        return files

    async def get_output_files_ordered(self, prompt_id: str) -> list[dict]:
        """Get output files ordered by node_id (for merged workflows).

        Each file dict includes a '_node_id' key so callers can match
        outputs to specific segments.
        """
        history = await self.get_history(prompt_id)
        if not history:
            return []
        outputs = history.get("outputs", {})
        files = []
        for node_id, node_output in sorted(outputs.items(), key=lambda x: int(x[0])):
            for f in node_output.get("gifs", []) + node_output.get("videos", []):
                f["_node_id"] = node_id
                files.append(f)
        return files

    async def download_file(self, filename: str, subfolder: str = "", file_type: str = "output") -> bytes:
        session = await self._get_session()
        params = {"filename": filename, "subfolder": subfolder, "type": file_type}
        async with session.get(f"{self.base_url}/view", params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to download {filename}")
            return await resp.read()

    async def upload_image(self, image_data: bytes, filename: str) -> dict:
        session = await self._get_session()
        form = aiohttp.FormData()
        form.add_field("image", image_data, filename=filename, content_type="image/png")
        form.add_field("overwrite", "true")
        async with session.post(f"{self.base_url}/upload/image", data=form) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to upload image")
            return await resp.json()

    async def interrupt(self):
        session = await self._get_session()
        async with session.post(f"{self.base_url}/interrupt") as resp:
            return resp.status == 200

    async def cancel_prompt(self, prompt_id: str):
        session = await self._get_session()
        payload = {"delete": [prompt_id]}
        async with session.post(f"{self.base_url}/queue", json=payload) as resp:
            return resp.status == 200

    async def wait_for_completion(self, prompt_id: str, timeout: float = 600) -> dict:
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        try:
            import websockets
            async with websockets.connect(f"{ws_url}/ws?clientId=api-{prompt_id}") as ws:
                deadline = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        data = json.loads(msg)
                        if data.get("type") == "executing":
                            ed = data.get("data", {})
                            if ed.get("prompt_id") == prompt_id and ed.get("node") is None:
                                return await self.get_history(prompt_id)
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            logger.warning(f"WebSocket failed, falling back to polling: {e}")
        # Polling fallback
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            history = await self.get_history(prompt_id)
            if history and history.get("status", {}).get("completed", False):
                return history
            if history and history.get("outputs"):
                return history
            await asyncio.sleep(2)
        raise TimeoutError(f"Prompt {prompt_id} timed out after {timeout}s")
