"""BytePlus SeeDream API client for image generation."""

import logging

import aiohttp

logger = logging.getLogger(__name__)


class BytePlusClient:
    """Async client for the BytePlus SeeDream image generation API."""

    def __init__(self, api_key: str, endpoint: str, model: str) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model

    async def generate_image(
        self,
        prompt: str,
        images: list[dict] | None = None,
        size: str = "1024x1024",
        seed: int | None = None,
    ) -> str:
        """Call SeeDream API and return the URL of the result image.

        Args:
            prompt: Text prompt for generation.
            images: Optional list of reference image dicts (each with "url" key
                    and optional "image_ratio" key for blending strength).
            size: Output image size as "WIDTHxHEIGHT".
            seed: Optional reproducibility seed.

        Returns:
            URL string of the generated image.

        Raises:
            RuntimeError: On HTTP errors or unexpected response format.
        """
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "response_format": "url",
        }
        if images:
            payload["image"] = images
        if seed is not None:
            payload["seed"] = seed

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        timeout = aiohttp.ClientTimeout(total=300)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.endpoint, json=payload, headers=headers
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            "BytePlus API error %d: %s",
                            resp.status,
                            body[:500],
                        )
                        raise RuntimeError(
                            f"BytePlus API error {resp.status}: {body[:500]}"
                        )

                    data = await resp.json()
        except aiohttp.ClientError as exc:
            logger.error("BytePlus API request failed: %s", exc)
            raise RuntimeError(f"BytePlus API request failed: {exc}") from exc

        try:
            return data["data"][0]["url"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected BytePlus response: %s", data)
            raise RuntimeError(
                f"Unexpected BytePlus response format: {exc}"
            ) from exc
