"""Event-driven chain orchestrator -- runs on API gateway side.

Coordinates multi-segment video generation by:
1. Submitting segment tasks one at a time to GPU workers via Redis
2. Waiting for completion via Redis polling
3. Processing intermediate results (last frame extraction, VLM/LLM)
4. Building next segment's workflow
5. Continuing until all segments complete
"""

import asyncio
import json
import logging
import time
from typing import Optional

from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.redis_keys import chain_key
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

# Timeout for waiting for a single segment to complete
SEGMENT_TIMEOUT = 1800  # 30 minutes
# Polling interval when waiting for task completion
POLL_INTERVAL = 2.0  # seconds


class ChainOrchestrator:
    """Orchestrates multi-segment video generation chains.

    Each chain is driven by a single asyncio.Task that loops through
    segments sequentially, creating one GPU task per segment and
    waiting for it to finish before moving on.
    """

    def __init__(
        self,
        gateway: TaskGateway,
        redis,  # async Redis connection
        vision_api_key: str = "",
        vision_base_url: str = "",
        vision_model: str = "",
        llm_api_key: str = "",
        llm_base_url: str = "",
        llm_model: str = "",
    ) -> None:
        self.gateway = gateway
        self.redis = redis
        self._vision_api_key = vision_api_key
        self._vision_base_url = vision_base_url
        self._vision_model = vision_model
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_model = llm_model
        self._active_chains: dict[str, asyncio.Task] = {}

    # -- Public API --------------------------------------------------------

    async def start_chain(
        self,
        chain_id: str,
        segments: list[dict],
        model: ModelType = ModelType.A14B,
        auto_continue: bool = False,
    ) -> None:
        """Start orchestrating a chain as a background asyncio task."""
        task = asyncio.create_task(
            self._run_chain(chain_id, segments, model, auto_continue)
        )
        self._active_chains[chain_id] = task
        task.add_done_callback(lambda _: self._active_chains.pop(chain_id, None))

    async def cancel_chain(self, chain_id: str) -> bool:
        """Cancel an active chain orchestration.

        Cancels the asyncio task and marks the chain as failed in Redis.
        Any queued segment tasks are also cancelled.
        """
        # Cancel the background asyncio task if it exists
        task = self._active_chains.get(chain_id)
        if task and not task.done():
            task.cancel()

        # Read chain data to get segment task IDs
        data = await self.redis.hgetall(chain_key(chain_id))
        if not data:
            return False

        status = data.get("status")
        if status not in ("running", "queued"):
            return False

        # Cancel any queued segment tasks
        task_ids_raw = data.get("segment_task_ids", "[]")
        try:
            task_ids = json.loads(task_ids_raw)
        except (json.JSONDecodeError, TypeError):
            task_ids = []

        for tid in task_ids:
            await self.gateway.cancel_queued_task(tid)

        # Mark chain as failed
        await self.redis.hset(chain_key(chain_id), mapping={
            "status": "failed",
            "error": "Cancelled by user",
            "completed_at": str(int(time.time())),
        })
        return True

    # -- Internal orchestration loop ---------------------------------------

    async def _run_chain(
        self,
        chain_id: str,
        segments: list[dict],
        model: ModelType,
        auto_continue: bool,
    ) -> None:
        """Main chain orchestration loop.

        Iterates through segments, creating a task for each and waiting
        for completion before proceeding to the next.
        """
        task_ids: list[str] = []
        video_urls: list[str] = []

        try:
            await self.redis.hset(chain_key(chain_id), mapping={
                "status": "running",
            })

            for i, segment in enumerate(segments):
                await self.redis.hset(chain_key(chain_id), mapping={
                    "current_segment": str(i),
                })

                # Determine generation mode
                mode = _determine_mode(i, segment)

                # Build workflow for this segment
                workflow = segment.get("workflow") or {
                    "_meta": {"version": "chain_v1", "segment": i},
                    "prompt": segment.get("prompt", ""),
                    "mode": mode.value,
                    "model": model.value,
                }

                # Build task params
                params = {
                    "prompt": segment.get("prompt", ""),
                    "segment_index": i,
                    "chain_id": chain_id,
                }
                # Request last frame extraction for non-final segments
                if i < len(segments) - 1 or segment.get("extract_last_frame", False):
                    params["extract_last_frame"] = True

                # Create and enqueue the task
                task_id = await self.gateway.create_task(
                    mode=mode,
                    model=model,
                    workflow=workflow,
                    params=params,
                    chain_id=chain_id,
                )
                task_ids.append(task_id)

                await self.redis.hset(chain_key(chain_id), mapping={
                    "segment_task_ids": json.dumps(task_ids),
                    "current_task_id": task_id,
                })

                # Wait for the task to reach a terminal state
                result = await self._wait_for_task(task_id, timeout=SEGMENT_TIMEOUT)

                if result["status"] == TaskStatus.FAILED.value:
                    error_msg = result.get("error") or "unknown error"
                    raise RuntimeError(
                        f"Segment {i} failed: {error_msg}"
                    )

                video_url = result.get("video_url") or ""
                video_urls.append(video_url)

                await self.redis.hset(chain_key(chain_id), mapping={
                    "completed_segments": str(i + 1),
                })

                # For non-final segments with auto_continue: use VLM/LLM
                if i < len(segments) - 1 and auto_continue:
                    last_frame_url = result.get("last_frame_url")
                    if last_frame_url and self._vision_api_key:
                        from api_gateway.services.continuation import (
                            describe_frame,
                            generate_continuation_prompt,
                        )
                        description = await describe_frame(
                            image_url=last_frame_url,
                            api_key=self._vision_api_key,
                            base_url=self._vision_base_url,
                            model=self._vision_model,
                        )
                        if description and self._llm_api_key:
                            next_prompt = await generate_continuation_prompt(
                                frame_description=description,
                                previous_prompt=segment.get("prompt", ""),
                                api_key=self._llm_api_key,
                                base_url=self._llm_base_url,
                                model=self._llm_model,
                            )
                            if next_prompt:
                                segments[i + 1]["prompt"] = next_prompt
                                logger.info(
                                    "Chain %s segment %d: auto-continue prompt set: %s",
                                    chain_id, i + 1, next_prompt
                                )

            # All segments completed successfully
            final_video_url = video_urls[-1] if video_urls else ""

            await self.redis.hset(chain_key(chain_id), mapping={
                "status": "completed",
                "final_video_url": final_video_url,
                "completed_at": str(int(time.time())),
            })
            logger.info(
                "Chain %s completed with %d segments", chain_id, len(segments)
            )

        except asyncio.CancelledError:
            await self.redis.hset(chain_key(chain_id), mapping={
                "status": "failed",
                "error": "Cancelled",
                "completed_at": str(int(time.time())),
            })
            # Cancel any queued segment tasks
            for tid in task_ids:
                await self.gateway.cancel_queued_task(tid)
            raise

        except Exception as e:
            logger.exception("Chain %s failed: %s", chain_id, e)
            await self.redis.hset(chain_key(chain_id), mapping={
                "status": "failed",
                "error": str(e),
                "completed_at": str(int(time.time())),
            })

    async def _wait_for_task(
        self, task_id: str, timeout: float = SEGMENT_TIMEOUT
    ) -> dict:
        """Poll Redis until a task reaches a terminal state.

        Returns the task dict once completed or failed.
        Raises TimeoutError if the task doesn't finish within *timeout* seconds.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = await self.gateway.get_task(task_id)
            if task and task["status"] in (
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
            ):
                return task
            await asyncio.sleep(POLL_INTERVAL)
        raise TimeoutError(f"Task {task_id} timed out after {timeout}s")


# -- Module-level helpers --------------------------------------------------


def _determine_mode(segment_index: int, segment: dict) -> GenerateMode:
    """Decide whether a segment is T2V or I2V.

    First segment: T2V unless an image_filename is provided.
    Subsequent segments: always I2V (they use the last frame).
    """
    if segment_index == 0:
        if segment.get("image_filename"):
            return GenerateMode.I2V
        return GenerateMode.T2V
    return GenerateMode.I2V
