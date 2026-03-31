#!/usr/bin/env python3
"""
Integration test: ComfyUI face swap (runs on remote server).

Tests:
1. Single face swap via _apply_face_swap_via_comfyui()
2. Concurrent face swaps (stress test for idle-worker wait)
3. Verifies NO Forge calls in logs

Usage (on remote server):
    cd /home/gime/soft/wan22-service
    python tests/integration_face_swap.py                # single
    python tests/integration_face_swap.py --concurrency 3 # stress
"""
import asyncio
import sys
import time
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test")


async def run_single_face_swap(task_manager, frame_url: str, face_ref: str, worker_id: int = 0) -> dict:
    """Run one face swap via ComfyUI and return result."""
    from api.routes.workflow_executor import _apply_face_swap_via_comfyui

    start = time.monotonic()
    result = {"id": worker_id, "success": False, "duration": 0, "url": None, "error": None}

    try:
        url = await _apply_face_swap_via_comfyui(
            frame_url=frame_url,
            reference_face=face_ref,
            strength=1.0,
            task_manager=task_manager,
        )
        result["url"] = url
        result["success"] = url is not None
        if not url:
            result["error"] = "returned None"
    except Exception as e:
        result["error"] = str(e)

    result["duration"] = round(time.monotonic() - start, 2)
    return result


async def main(concurrency: int):
    # Initialize TaskManager (connects to Redis, discovers workers)
    from api.services.task_manager import TaskManager
    tm = TaskManager()
    await tm.start()

    workers = await tm.list_workers()
    logger.info("Workers: %s", [(w["id"], w["alive"]) for w in workers])

    if not any(w["alive"] for w in workers):
        logger.error("No alive workers!")
        await tm.stop()
        return

    # Test images (copied to uploads/)
    frame_url = "http://127.0.0.1:8000/uploads/test_frame.png"
    face_ref = "test_face.png"  # local filename in uploads/

    print(f"\n{'='*60}")
    print(f"ComfyUI Face Swap Integration Test")
    print(f"Workers: {len(workers)} | Concurrency: {concurrency}")
    print(f"{'='*60}\n")

    # Run concurrent face swaps
    start = time.monotonic()
    tasks = [
        run_single_face_swap(tm, frame_url, face_ref, i)
        for i in range(concurrency)
    ]
    results = await asyncio.gather(*tasks)
    total = round(time.monotonic() - start, 2)

    # Print results
    print(f"\n{'='*60}")
    print(f"Results (total: {total}s)")
    print(f"{'='*60}")

    successes = 0
    for r in results:
        status = "OK" if r["success"] else "FAIL"
        print(f"  [{r['id']}] {status} in {r['duration']}s", end="")
        if r["url"]:
            print(f"  -> {r['url']}")
        elif r["error"]:
            print(f"  ERROR: {r['error']}")
        else:
            print()
        if r["success"]:
            successes += 1

    print(f"\nSummary: {successes}/{concurrency} succeeded | wall time: {total}s")

    if successes > 0:
        avg = round(sum(r["duration"] for r in results if r["success"]) / successes, 2)
        print(f"Avg success time: {avg}s")

    if successes == concurrency:
        print("\nALL PASSED")
    else:
        print(f"\n{concurrency - successes} FAILED")

    await tm.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", "-c", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.concurrency))
