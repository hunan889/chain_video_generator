#!/usr/bin/env python3
"""Quick test for LoRA auto-selection endpoint."""
import sys
import httpx

API_URL = "http://127.0.0.1:8000"
API_KEY = "wan22-default-key-change-me"

HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

TEST_PROMPTS = [
    "A woman riding on top of a man, POV shot from below",
    "A beautiful girl with big breasts walking on the beach",
    "A man and woman in reverse suspended congress position",
    "A serene landscape with mountains and a flowing river",
    "She is giving him a blowjob while stroking with her hand",
]


def test_recommend(prompt: str):
    print(f"\n{'='*60}")
    print(f"Prompt: {prompt}")
    print("-" * 60)
    resp = httpx.post(
        f"{API_URL}/api/v1/loras/recommend",
        headers=HEADERS,
        json={"prompt": prompt},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"ERROR {resp.status_code}: {resp.text}")
        return
    data = resp.json()
    loras = data.get("loras", [])
    if not loras:
        print("Result: (no LoRA selected)")
    else:
        for l in loras:
            print(f"  -> {l['name']}  (strength: {l['strength']})")


def main():
    prompts = sys.argv[1:] if len(sys.argv) > 1 else TEST_PROMPTS
    print("LoRA Auto-Selection Test")
    for p in prompts:
        test_recommend(p)
    print(f"\n{'='*60}")
    print("Done.")


if __name__ == "__main__":
    main()
