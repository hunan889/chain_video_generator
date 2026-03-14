#!/usr/bin/env python3
"""Test the /api/v1/prompt/optimize endpoint."""
import asyncio
import httpx


async def test_api():
    base_url = "http://127.0.0.1:8000"
    api_key = "wan22-default-key-change-me"  # From config/api_keys.yaml

    test_prompt = "A sexy woman is having sex with a man in the face-down ass-up position"

    print("="*80)
    print("Testing /api/v1/prompt/optimize API")
    print("="*80)
    print(f"\nInput: {test_prompt}")
    print("\nSending request...")

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{base_url}/api/v1/prompt/optimize",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
            },
            json={
                "prompt": test_prompt,
                "lora_names": [],
                "mode": "t2v",
                "duration": 6,
            }
        )

        print(f"\nStatus: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"\n{'='*80}")
            print("Optimized Prompt:")
            print(f"{'='*80}")
            print(data['optimized_prompt'])

            print(f"\n{'='*80}")
            print("Analysis:")
            print(f"{'='*80}")

            # Check for explicit details
            optimized = data['optimized_prompt'].lower()
            explicit_terms = ['cock', 'pussy', 'ass', 'wet', 'penetrat', 'thrust', 'dripping']
            found = [term for term in explicit_terms if term in optimized]

            print(f"Word count: {len(data['optimized_prompt'].split())} words")
            print(f"Explicit terms found: {', '.join(found)}")
            print(f"Original keywords preserved: {'face-down ass-up' in optimized}")

            if data.get('explanation'):
                print(f"\nExplanation: {data['explanation']}")

            # Check if it's the new detailed version
            if len(found) >= 3 and len(data['optimized_prompt'].split()) > 80:
                print("\n✅ SUCCESS: Using new detailed expansion!")
            else:
                print("\n⚠️  WARNING: May still be using old version")
        else:
            print(f"\nError: {response.text}")


if __name__ == "__main__":
    asyncio.run(test_api())
