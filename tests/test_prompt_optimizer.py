#!/usr/bin/env python3
"""Test script for the optimized prompt optimizer."""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from api.services.prompt_optimizer import PromptOptimizer


async def test_optimizer():
    optimizer = PromptOptimizer()

    # Test case 1: Original example from user
    test_cases = [
        {
            "name": "Face-down ass-up position",
            "prompt": "A sexy woman is having sex with a man in the face-down ass-up position",
            "mode": "t2v",
            "duration": 6,
        },
        {
            "name": "Multiple actions",
            "prompt": "girl takes off top, does blowjob, he cums on face",
            "mode": "t2v",
            "duration": 9,
        },
        {
            "name": "Simple action",
            "prompt": "A woman dancing sensually",
            "mode": "t2v",
            "duration": 4,
        },
    ]

    for i, test in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"Test Case {i}: {test['name']}")
        print(f"{'='*80}")
        print(f"Original: {test['prompt']}")
        print(f"Mode: {test['mode']}, Duration: {test['duration']}s")
        print(f"\n{'-'*80}")

        try:
            result = await optimizer.optimize(
                prompt=test['prompt'],
                trigger_words=[],
                mode=test['mode'],
                duration=test['duration'],
            )

            print(f"Optimized:\n{result['optimized_prompt']}")
            if result.get('explanation'):
                print(f"\nExplanation: {result['explanation']}")

            # Check if original keywords are preserved
            original_lower = test['prompt'].lower()
            optimized_lower = result['optimized_prompt'].lower()

            # Extract key terms
            key_terms = []
            if 'face-down ass-up' in original_lower:
                key_terms.append('face-down ass-up')
            if 'blowjob' in original_lower:
                key_terms.append('blowjob')
            if 'cums on face' in original_lower or 'cum on face' in original_lower:
                key_terms.append('cum')
            if 'takes off top' in original_lower:
                key_terms.append('takes off top')

            print(f"\n{'-'*80}")
            print("Preservation Check:")
            for term in key_terms:
                if term in optimized_lower:
                    print(f"  ✓ '{term}' preserved")
                else:
                    print(f"  ✗ '{term}' MISSING!")

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_optimizer())
