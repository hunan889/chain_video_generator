"""Inference worker main loop — BLPOP queue:inference and dispatch by mode."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from shared.enums import GenerateMode
from shared.redis_keys import inference_queue_key, task_key

from gpu.inference_worker.handlers import chat as chat_handler
from gpu.inference_worker.handlers import describe_image as describe_handler
from gpu.inference_worker.handlers import embed as embed_handler

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from gpu.inference_worker.config import InferenceWorkerConfig

logger = logging.getLogger(__name__)


class InferenceWorker:
    """Polls ``queue:inference`` for tasks and dispatches them by mode."""

    def __init__(self, config: "InferenceWorkerConfig", redis: "Redis") -> None:
        self._config = config
        self._redis = redis
        self._running: bool = False
        self._queue = inference_queue_key()

    async def start(self) -> None:
        """Pre-load models so the first user request doesn't pay the cost."""
        logger.info(
            "Pre-loading embedding model %s on %s ...",
            self._config.embedding_model,
            self._config.embedding_device,
        )
        try:
            handler = embed_handler.get_handler(
                self._config.embedding_model,
                self._config.embedding_device,
                self._config.embedding_batch_size,
            )
            await handler.ensure_loaded()
        except Exception:
            logger.exception("Failed to pre-load embedding model (will retry on first request)")

    async def stop(self) -> None:
        """Request graceful shutdown."""
        self._running = False

    async def close(self) -> None:
        """Release resources held by handlers."""
        try:
            await chat_handler.close()
        except Exception:
            logger.exception("chat_handler.close failed")
        try:
            await describe_handler.close()
        except Exception:
            logger.exception("describe_handler.close failed")

    async def run(self) -> None:
        """Main loop. Blocks on BLPOP and processes one task at a time."""
        self._running = True
        logger.info(
            "InferenceWorker %s started, polling %s",
            self._config.worker_id, self._queue,
        )
        while self._running:
            try:
                result = await self._redis.blpop(
                    self._queue, timeout=self._config.queue_blpop_timeout,
                )
            except Exception:
                logger.exception("BLPOP failed (will retry)")
                await asyncio.sleep(1)
                continue
            if result is None:
                continue

            _queue_name, task_id = result
            try:
                await self._process_task(task_id)
            except Exception as exc:
                # Last-resort error reporting; per-task errors are caught
                # inside _process_task and written back to the hash.
                logger.exception("Unhandled error processing %s: %s", task_id, exc)

        logger.info("InferenceWorker %s stopped", self._config.worker_id)

    async def _process_task(self, task_id: str) -> None:
        tk = task_key(task_id)
        raw = await self._redis.hgetall(tk)
        if not raw:
            logger.warning("Task %s not found in Redis (expired?)", task_id)
            return

        mode = raw.get("mode", "")
        payload_raw = raw.get("payload", "")
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError as exc:
            await self._mark_failed(tk, f"invalid payload JSON: {exc}")
            return

        await self._redis.hset(tk, mapping={
            "status": "running",
            "started_at": str(int(time.time())),
        })

        t0 = time.time()
        try:
            result = await self._dispatch(mode, payload)
        except Exception as exc:
            logger.exception("Task %s (mode=%s) failed: %s", task_id, mode, exc)
            await self._mark_failed(tk, f"{type(exc).__name__}: {exc}")
            return

        dt_ms = int((time.time() - t0) * 1000)
        await self._redis.hset(tk, mapping={
            "status": "completed",
            "result": json.dumps(result),
            "completed_at": str(int(time.time())),
            "duration_ms": str(dt_ms),
        })
        logger.info(
            "Task %s mode=%s done in %d ms", task_id, mode, dt_ms,
        )

    async def _dispatch(self, mode: str, payload: dict) -> dict:
        if mode == GenerateMode.INFERENCE_EMBED.value:
            return await embed_handler.handle(
                payload,
                model_name=self._config.embedding_model,
                device=self._config.embedding_device,
                batch_size=self._config.embedding_batch_size,
            )
        if mode == GenerateMode.INFERENCE_DESCRIBE.value:
            return await describe_handler.handle(
                payload,
                base_url=self._config.vlm_base_url,
                default_model=self._config.vlm_model,
            )
        if mode == GenerateMode.INFERENCE_CHAT.value:
            return await chat_handler.handle(
                payload,
                base_url=self._config.llm_base_url,
                default_model=self._config.llm_model,
            )
        raise ValueError(f"unknown inference mode: {mode!r}")

    async def _mark_failed(self, tk: str, error: str) -> None:
        await self._redis.hset(tk, mapping={
            "status": "failed",
            "error": error,
            "completed_at": str(int(time.time())),
        })
