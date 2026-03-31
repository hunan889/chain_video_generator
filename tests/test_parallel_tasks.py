"""
Test: Submit multiple tasks via the frontend at http://148.153.121.44:8080/
using Playwright to interact with the actual UI.
"""
import asyncio
import time
from playwright.async_api import async_playwright

BASE_URL = "http://148.153.121.44:8080"
NUM_TASKS = 3
PROMPTS = [
    "a cat walking on the beach, sunny day",
    "a girl dancing in the rain, cinematic",
    "a spaceship flying through clouds, epic",
]


async def main():
    print("=" * 60)
    print("Frontend Parallel Task Test (8080 UI)")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 1. Open frontend
        print("\n[1] Opening http://148.153.121.44:8080/ ...")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        title = await page.title()
        print(f"    Page title: {title}")

        # Wait for the form to be ready
        await page.wait_for_selector("#wan22-prompt", timeout=10000)
        print("    Form loaded.")

        # Check default values
        duration = await page.locator("#wan22-duration").input_value()
        resolution = await page.locator("#wan22-resolution").input_value()
        aspect = await page.locator("#wan22-aspect-ratio").input_value()
        print(f"    Defaults: duration={duration}s, resolution={resolution}, aspect={aspect}")

        # 2. Submit tasks by filling prompt and clicking submit
        print(f"\n[2] Submitting {NUM_TASKS} tasks via UI...")
        task_ids = []
        t_start = time.time()

        for i in range(NUM_TASKS):
            # Fill prompt
            await page.locator("#wan22-prompt").fill(PROMPTS[i])

            # Intercept the API response to capture task_id
            async with page.expect_response(
                lambda r: "/api/wan22/generate" in r.url, timeout=30000
            ) as response_info:
                # Click submit button
                await page.locator("#wan22-submit").click()
                response = await response_info.value

            data = await response.json()
            if data.get("success"):
                tid = data.get("task_id", "?")
                task_ids.append(tid)
                print(f"    Task {i}: '{PROMPTS[i][:40]}' -> task_id={tid[:16]}...")
            else:
                print(f"    Task {i}: FAILED - {data.get('error', 'unknown')}")

            # Wait for the submit button to re-enable
            await page.wait_for_function(
                "() => !document.getElementById('wan22-submit').disabled",
                timeout=10000,
            )

        t_submit = time.time() - t_start
        print(f"    All submitted in {t_submit:.1f}s, got {len(task_ids)} task IDs")

        if not task_ids:
            print("\n    FATAL: No tasks submitted successfully!")
            await browser.close()
            return

        # 3. Monitor task statuses
        # Use the same query API that the 8080 frontend uses
        print(f"\n[3] Monitoring {len(task_ids)} tasks (polling every 5s, max 10min)...")
        print()

        max_wait = 600
        start_time = time.time()
        max_concurrent = 0
        seen_parallel = False

        while time.time() - start_time < max_wait:
            elapsed = time.time() - start_time

            statuses = await page.evaluate(
                """async (taskIds) => {
                    const results = {};
                    for (const tid of taskIds) {
                        try {
                            const r = await fetch('/api/wan22/query-task/' + tid);
                            const d = await r.json();
                            results[tid] = {
                                status: d.status || d.state || 'unknown',
                                progress: d.progress || 0,
                                stage: d.stage || d.current_stage || '',
                                video: d.video_url || d.output_url || ''
                            };
                        } catch(e) {
                            results[tid] = {status: 'error', progress: 0, stage: '', video: ''};
                        }
                    }
                    return results;
                }""",
                task_ids,
            )

            running = 0
            queued = 0
            done = 0
            failed = 0
            for s in statuses.values():
                st = s["status"].lower()
                if st in ("running", "processing", "generating"):
                    running += 1
                elif st in ("queued", "pending", "waiting"):
                    queued += 1
                elif st in ("completed", "done", "success", "finished"):
                    done += 1
                elif st in ("failed", "error"):
                    failed += 1
                else:
                    queued += 1  # unknown = treat as queued

            if running > max_concurrent:
                max_concurrent = running
            if running >= 2:
                seen_parallel = True

            parts = []
            for tid in task_ids:
                s = statuses.get(tid, {})
                short = tid[:10]
                pct = int(s.get("progress", 0) * 100) if isinstance(s.get("progress", 0), float) and s.get("progress", 0) <= 1 else int(s.get("progress", 0))
                stage = s.get("stage", "")
                stage_str = f",{stage}" if stage else ""
                parts.append(f"{short}={s.get('status','?')}({pct}%{stage_str})")

            print(f"  [{elapsed:5.1f}s] R:{running} Q:{queued} D:{done} F:{failed} | {' | '.join(parts)}")

            if done + failed >= len(task_ids):
                print("\n    All tasks finished!")
                break

            await asyncio.sleep(5)

        total_time = time.time() - start_time

        # 4. Results
        print()
        print("=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(f"  Tasks submitted:             {len(task_ids)}")
        print(f"  Max concurrent running:      {max_concurrent}")
        print(f"  Parallel execution observed: {'YES' if seen_parallel else 'NO'}")
        print(f"  Total elapsed:               {total_time:.1f}s")
        print()

        # Show final video URLs
        print("  Final status:")
        for tid in task_ids:
            s = statuses.get(tid, {})
            vid = s.get("video", "")
            print(f"    {tid[:16]}  {s.get('status','')}  {vid[:60] if vid else 'no video'}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
