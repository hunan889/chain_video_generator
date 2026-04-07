"""Entry point for the inference worker process.

Usage::

    python -m gpu.inference_worker.main
"""

from __future__ import annotations

import asyncio
import logging
import signal

import redis.asyncio as aioredis

from gpu.inference_worker.config import load_config
from gpu.inference_worker.worker import InferenceWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    logger.info(
        "Starting inference worker %s (embedding=%s on %s, llm=%s, vlm=%s)",
        config.worker_id,
        config.embedding_model,
        config.embedding_device,
        config.llm_base_url,
        config.vlm_base_url,
    )

    redis_conn = aioredis.from_url(config.redis_url, decode_responses=True)
    worker = InferenceWorker(config=config, redis=redis_conn)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(_shutdown(worker)),
        )

    await worker.start()
    try:
        await worker.run()
    finally:
        await worker.close()
        await redis_conn.close()
        logger.info("Inference worker %s shut down cleanly", config.worker_id)


async def _shutdown(worker: InferenceWorker) -> None:
    logger.info("Shutdown signal received")
    await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
