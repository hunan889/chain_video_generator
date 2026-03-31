#!/usr/bin/env python3
"""
Stress test for ComfyUI face swap.

Usage:
    # Basic test — single face swap request
    python tests/stress_test_face_swap.py

    # Concurrency test — 5 parallel requests (tests idle-worker waiting)
    python tests/stress_test_face_swap.py --concurrency 5

    # Custom server
    python tests/stress_test_face_swap.py --server http://148.153.121.44:8000 --concurrency 3

What this tests:
1. Face swap via ComfyUI works end-to-end
2. Multiple concurrent requests correctly queue/wait for idle workers
3. No Forge calls are made (verify in server logs)
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

SERVER = "http://148.153.121.44:8000"

# A minimal 4x4 red PNG (valid image for face swap testing)
# In practice, face swap needs a real face. We'll use a test image if available,
# otherwise fall back to uploading a sample.
SAMPLE_FACE_B64 = None  # Will be loaded from file or generated


async def upload_test_image(session: aiohttp.ClientSession, server: str, image_path: str) -> str:
    """Upload a test image and return its URL."""
    with open(image_path, "rb") as f:
        data = aiohttp.FormData()
        data.add_field("file", f, filename=os.path.basename(image_path), content_type="image/png")
        async with session.post(f"{server}/api/v1/upload", data=data) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Upload failed: {resp.status} {await resp.text()}")
            result = await resp.json()
            return result.get("url") or result.get("filename")


async def create_test_images(session: aiohttp.ClientSession, server: str) -> tuple[str, str]:
    """Generate test images via T2I or use existing uploads.

    Returns (frame_url, face_reference).
    """
    # Try to use the T2I endpoint to generate a frame with a face
    print("  Generating test frame via T2I (portrait of a person)...")
    t2i_payload = {
        "prompt": "portrait photo of a young woman, front view, neutral background, high quality",
        "negative_prompt": "blurry, low quality",
        "width": 480,
        "height": 640,
        "steps": 15,
        "cfg_scale": 7.0,
        "sampler": "DPM++ 2M Karras",
        "seed": 42,
    }

    async with session.post(f"{server}/api/v1/image/generate", json=t2i_payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
        if resp.status == 200:
            result = await resp.json()
            frame_url = result.get("url") or result.get("image_url")
            if frame_url:
                print(f"  Frame generated: {frame_url}")
            else:
                print(f"  T2I response (no url field): {json.dumps(result, indent=2)[:200]}")
                frame_url = None
        else:
            print(f"  T2I failed ({resp.status}), will try alternative...")
            frame_url = None

    # Generate a second image as the face reference
    if frame_url:
        print("  Generating reference face image...")
        t2i_payload["prompt"] = "close-up portrait of a different young woman, front view, studio lighting"
        t2i_payload["seed"] = 123
        async with session.post(f"{server}/api/v1/image/generate", json=t2i_payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status == 200:
                result = await resp.json()
                face_url = result.get("url") or result.get("image_url")
                if face_url:
                    print(f"  Face reference generated: {face_url}")
                    return frame_url, face_url

    # Fallback: use any existing uploaded images
    print("  T2I not available, checking for existing uploads...")
    async with session.get(f"{server}/api/v1/results?limit=2") as resp:
        if resp.status == 200:
            results = await resp.json()
            items = results if isinstance(results, list) else results.get("items", [])
            if len(items) >= 2:
                frame_url = items[0].get("url", items[0].get("path"))
                face_url = items[1].get("url", items[1].get("path"))
                print(f"  Using existing: frame={frame_url}, face={face_url}")
                return frame_url, face_url

    raise RuntimeError(
        "Cannot create test images. Please ensure T2I endpoint or uploaded images are available.\n"
        "You can manually provide images via --frame and --face arguments."
    )


async def test_face_swap_direct(
    session: aiohttp.ClientSession,
    server: str,
    frame_url: str,
    face_reference: str,
    worker_id: int,
) -> dict:
    """Test face swap by calling the workflow/test/stage2 endpoint or the workflow API directly.

    Returns result dict with timing and status.
    """
    start = time.monotonic()
    result = {"worker_id": worker_id, "success": False, "duration": 0, "error": None, "url": None}

    # Use the advanced workflow API with face_reference mode + face_swap enabled
    payload = {
        "user_prompt": "a beautiful portrait, natural lighting",
        "mode": "face_reference",
        "reference_image": face_reference,
        "uploaded_first_frame": frame_url,
        "internal_config": {
            "stage1_prompt_analysis": {
                "auto_analyze": False,
                "auto_lora": False,
                "auto_prompt": False,
            },
            "stage2_first_frame": {
                "first_frame_source": "upload",
                "face_swap": {
                    "enabled": True,
                    "strength": 1.0,
                },
            },
            "stage3_seedream": {
                "enabled": False,  # Skip SeeDream, we only test face swap
            },
            "stage4_video": {
                "enabled": False,  # Skip video generation
            },
        },
    }

    try:
        print(f"  [Worker {worker_id}] Submitting face swap request...")
        async with session.post(
            f"{server}/api/v1/workflow/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                workflow_id = data.get("workflow_id")
                print(f"  [Worker {worker_id}] Workflow submitted: {workflow_id}")

                # Poll workflow status
                if workflow_id:
                    result = await _poll_workflow(session, server, workflow_id, worker_id, start)
                else:
                    result["error"] = f"No workflow_id in response: {data}"
            else:
                text = await resp.text()
                result["error"] = f"HTTP {resp.status}: {text[:200]}"
    except Exception as e:
        result["error"] = str(e)

    result["duration"] = round(time.monotonic() - start, 2)
    return result


async def _poll_workflow(
    session: aiohttp.ClientSession,
    server: str,
    workflow_id: str,
    worker_id: int,
    start: float,
) -> dict:
    """Poll workflow status until face swap stage completes."""
    result = {"worker_id": worker_id, "success": False, "duration": 0, "error": None, "url": None}
    deadline = time.monotonic() + 180  # 3 minute timeout

    while time.monotonic() < deadline:
        try:
            async with session.get(
                f"{server}/api/v1/workflow/{workflow_id}/status",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    await asyncio.sleep(2)
                    continue
                status = await resp.json()

                stages = status.get("stages", {})
                current = status.get("current_stage", "")

                # Check if face swap completed (in first_frame_acquisition stage)
                ff_stage = stages.get("first_frame_acquisition", {})
                if ff_stage.get("status") == "completed":
                    details = ff_stage.get("details", {})
                    if details.get("face_swapped"):
                        result["success"] = True
                        result["url"] = details.get("url")
                        elapsed = round(time.monotonic() - start, 2)
                        print(f"  [Worker {worker_id}] Face swap SUCCESS in {elapsed}s → {result['url']}")
                        return result
                    elif details.get("face_swap_skipped"):
                        result["error"] = f"Face swap skipped: {details.get('face_swap_skip_reason')}"
                        return result

                # Check for workflow-level failure
                wf_status = status.get("status")
                if wf_status in ("failed", "error"):
                    result["error"] = status.get("error", "workflow failed")
                    return result

                # If we're past stage 2, face swap either happened or didn't
                if current in ("seedream_edit", "video_generation", "completed"):
                    if not ff_stage:
                        result["error"] = "first_frame_acquisition stage missing"
                    else:
                        result["error"] = f"Face swap not applied. Stage details: {json.dumps(ff_stage)[:200]}"
                    return result

        except Exception as e:
            print(f"  [Worker {worker_id}] Poll error: {e}")

        await asyncio.sleep(2)

    result["error"] = "Polling timeout (180s)"
    return result


async def run_stress_test(server: str, concurrency: int, frame_url: str = None, face_url: str = None):
    """Run the stress test."""
    print(f"\n{'='*60}")
    print(f"Face Swap Stress Test (ComfyUI)")
    print(f"Server:      {server}")
    print(f"Concurrency: {concurrency}")
    print(f"{'='*60}\n")

    async with aiohttp.ClientSession() as session:
        # Step 1: Prepare test images
        if frame_url and face_url:
            print(f"Using provided images: frame={frame_url}, face={face_url}")
        else:
            print("Step 1: Preparing test images...")
            try:
                frame_url, face_url = await create_test_images(session, server)
            except RuntimeError as e:
                print(f"\nERROR: {e}")
                return

        print(f"\nStep 2: Running {concurrency} concurrent face swap request(s)...\n")
        start = time.monotonic()

        # Launch concurrent requests
        tasks = [
            test_face_swap_direct(session, server, frame_url, face_url, i)
            for i in range(concurrency)
        ]
        results = await asyncio.gather(*tasks)

        total_time = round(time.monotonic() - start, 2)

        # Step 3: Print results
        print(f"\n{'='*60}")
        print(f"Results (total time: {total_time}s)")
        print(f"{'='*60}")

        successes = sum(1 for r in results if r["success"])
        failures = sum(1 for r in results if not r["success"])

        for r in results:
            status = "OK" if r["success"] else "FAIL"
            print(f"  Worker {r['worker_id']}: [{status}] {r['duration']}s", end="")
            if r["error"]:
                print(f" — {r['error'][:80]}")
            elif r["url"]:
                print(f" → {r['url']}")
            else:
                print()

        print(f"\nSummary: {successes}/{concurrency} succeeded, {failures} failed")
        print(f"Total wall time: {total_time}s")
        if successes > 0:
            avg = round(sum(r["duration"] for r in results if r["success"]) / successes, 2)
            print(f"Average success time: {avg}s")

        print(f"\nNext steps:")
        print(f"  1. Check server logs for 'ComfyUI face swap' messages")
        print(f"  2. Verify NO 'reactor/image' (Forge) calls in logs")
        print(f"  3. If concurrency > #workers, verify 'all workers busy' log entries")

        return results


def main():
    parser = argparse.ArgumentParser(description="Face swap stress test (ComfyUI)")
    parser.add_argument("--server", default=SERVER, help=f"API server URL (default: {SERVER})")
    parser.add_argument("--concurrency", "-c", type=int, default=1, help="Number of concurrent requests (default: 1)")
    parser.add_argument("--frame", default=None, help="Frame image URL (skip auto-generation)")
    parser.add_argument("--face", default=None, help="Face reference URL (skip auto-generation)")
    args = parser.parse_args()

    asyncio.run(run_stress_test(args.server, args.concurrency, args.frame, args.face))


if __name__ == "__main__":
    main()
