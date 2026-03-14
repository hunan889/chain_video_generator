#!/usr/bin/env python3
"""Test T2V generation endpoint to debug 'Missing request parameters' error."""
import asyncio
import httpx
import json


async def test_t2v_generation():
    base_url = "http://127.0.0.1:8000"
    api_key = "wan22-default-key-change-me"

    # Simulate frontend request
    body = {
        "prompt": "A woman dancing",
        "negative_prompt": "",
        "model": "a14b",
        "width": 832,
        "height": 480,
        "num_frames": 97,
        "fps": 24,
        "steps": 30,
        "cfg": 7.0,
        "shift": 1.0,
        "seed": None,
        "scheduler": "unipc",  # Fixed: use valid scheduler
        "model_preset": "high",
        "t5_preset": "high",
        "loras": [],
        "upscale": False,
        "auto_lora": False,
        "auto_prompt": False
    }

    print("="*80)
    print("Testing /api/v1/generate (T2V)")
    print("="*80)
    print(f"\nRequest body:")
    print(json.dumps(body, indent=2))
    print("\nSending request...")

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{base_url}/api/v1/generate",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
            },
            json=body
        )

        print(f"\nStatus: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ SUCCESS!")
            print(f"Task ID: {data.get('task_id')}")
            print(f"Status: {data.get('status')}")
        else:
            print(f"\n❌ ERROR:")
            print(response.text)


if __name__ == "__main__":
    asyncio.run(test_t2v_generation())
