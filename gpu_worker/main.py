"""Entry point for the GPU worker process.

Usage:
    python -m gpu_worker.main
"""

import asyncio
import logging
import signal

import redis.asyncio as aioredis

from gpu_worker.config import load_config
from gpu_worker.heartbeat import HeartbeatReporter
from gpu_worker.worker import GPUWorker
from shared.cos import COSClient, COSConfig
from shared.task_gateway import TaskGateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    logger.info(
        "Starting GPU worker %s (models: %s)",
        config.worker_id,
        config.model_keys,
    )

    # Redis
    redis_conn = aioredis.from_url(config.redis_url, decode_responses=True)

    # TaskGateway
    gateway = TaskGateway(redis_conn, config.task_expiry)

    # COS client
    cos_config = COSConfig(
        secret_id=config.cos_secret_id,
        secret_key=config.cos_secret_key,
        bucket=config.cos_bucket,
        region=config.cos_region,
        prefix=config.cos_prefix,
        cdn_domain=config.cos_cdn_domain,
    )
    cos_client = COSClient(cos_config)

    # Heartbeat
    heartbeat = HeartbeatReporter(redis=redis_conn, config=config)

    # Worker
    worker = GPUWorker(
        config=config,
        redis=redis_conn,
        gateway=gateway,
        cos_client=cos_client,
        heartbeat=heartbeat,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(worker, heartbeat)))

    await heartbeat.start()
    try:
        worker._running = True
        await worker.run()
    finally:
        await worker.close()
        await heartbeat.stop()
        await redis_conn.close()
        logger.info("Worker %s shut down cleanly", config.worker_id)


async def _shutdown(worker: GPUWorker, heartbeat: HeartbeatReporter) -> None:
    """Handle graceful shutdown."""
    logger.info("Shutdown signal received")
    await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
