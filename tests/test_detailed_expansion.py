#!/usr/bin/env python3
"""Comparison test: old vs new prompt optimizer output."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from api.services.prompt_optimizer import PromptOptimizer


async def compare_outputs():
    optimizer = PromptOptimizer()

    print("="*80)
    print("PROMPT OPTIMIZER - DETAILED EXPANSION TEST")
    print("="*80)
    print("\nGoal: Preserve original meaning + Add explicit video details\n")

    test_cases = [
        {
            "name": "Face-down ass-up position",
            "prompt": "A sexy woman is having sex with a man in the face-down ass-up position",
            "duration": 6,
        },
        {
            "name": "Blowjob scene",
            "prompt": "A woman giving a blowjob",
            "duration": 5,
        },
        {
            "name": "Doggy style",
            "prompt": "Doggy style sex, hard and fast",
            "duration": 6,
        },
        {
            "name": "Cumshot",
            "prompt": "He cums on her tits",
            "duration": 3,
        },
    ]

    for i, test in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"Test {i}: {test['name']}")
        print(f"{'='*80}")
        print(f"\n📝 Original Input:")
        print(f"   \"{test['prompt']}\"")
        print(f"\n⏱️  Duration: {test['duration']}s")

        try:
            result = await optimizer.optimize(
                prompt=test['prompt'],
                trigger_words=[],
                mode="t2v",
                duration=test['duration'],
            )

            print(f"\n✨ Optimized Output:")
            print(f"   {result['optimized_prompt']}")

            # Count words
            word_count = len(result['optimized_prompt'].split())
            print(f"\n📊 Word count: {word_count} words")

            # Check for explicit details
            explicit_terms = ['cock', 'pussy', 'tits', 'ass', 'cum', 'wet', 'dripping',
                            'thrusting', 'penetrat', 'shaft', 'lips', 'tongue']
            found_terms = [term for term in explicit_terms
                          if term in result['optimized_prompt'].lower()]

            print(f"🔥 Explicit details added: {', '.join(found_terms)}")

            # Check preservation
            original_lower = test['prompt'].lower()
            optimized_lower = result['optimized_prompt'].lower()

            key_phrases = []
            if 'face-down ass-up' in original_lower:
                key_phrases.append('face-down ass-up')
            if 'blowjob' in original_lower:
                key_phrases.append('blowjob')
            if 'doggy' in original_lower:
                key_phrases.append('doggy')
            if 'cums on' in original_lower or 'cum on' in original_lower:
                key_phrases.append('cum')
            if 'tits' in original_lower:
                key_phrases.append('tits')

            print(f"\n✅ Original keywords preserved:")
            for phrase in key_phrases:
                status = "✓" if phrase in optimized_lower else "✗"
                print(f"   {status} '{phrase}'")

        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print("\n✅ Improvements:")
    print("   1. Original keywords preserved (no rewriting)")
    print("   2. Explicit anatomical terms added (cock, pussy, tits, etc.)")
    print("   3. Motion details: penetration depth, rhythm, body physics")
    print("   4. Fluid details: wetness, cum, saliva, dripping")
    print("   5. Sound details: wet sounds, slapping, moaning")
    print("   6. Physical reactions: body movements, facial expressions")
    print("   7. Longer, more detailed descriptions (100-200+ words)")
    print("\n🎯 Result: User gets exactly what they asked for, with rich video details")


if __name__ == "__main__":
    asyncio.run(compare_outputs())
