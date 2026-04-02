#!/usr/bin/env python3
"""
Comprehensive stress test for Wan2.2 Video Generation Service.

Tests service reliability under concurrent load across all major endpoints.

Usage:
    # Full stress test (default: 148.153.121.44:8000)
    python tests/stress_test_service.py

    # Custom server and concurrency
    python tests/stress_test_service.py --server http://148.153.121.44:8000 --concurrency 50

    # Quick smoke test (low concurrency)
    python tests/stress_test_service.py --quick

Test categories:
    1. Health endpoint stability under rapid fire
    2. Concurrent task creation (T2V)
    3. Concurrent task retrieval (GET)
    4. Concurrent task cancellation
    5. LoRA catalog reads under load
    6. Model presets / T5 presets reads
    7. Mixed read/write workload
    8. Connection exhaustion (many simultaneous connections)
    9. Error handling under load (invalid requests)
    10. Redis resilience (rapid task lifecycle)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SERVER = "http://148.153.121.44:8000"
DEFAULT_API_KEY = "wan22-default-key-change-me"
DEFAULT_CONCURRENCY = 50
QUICK_CONCURRENCY = 10
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
CONNECT_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)


# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------

@dataclass
class RequestResult:
    """Single request result."""
    endpoint: str
    method: str
    status: int
    latency_ms: float
    success: bool
    error: str | None = None


@dataclass
class TestResult:
    """Aggregated result for a test category."""
    name: str
    total_requests: int = 0
    successes: int = 0
    failures: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    status_codes: dict[int, int] = field(default_factory=dict)
    error_messages: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.successes / self.total_requests * 100) if self.total_requests > 0 else 0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0

    @property
    def rps(self) -> float:
        return self.total_requests / self.wall_time_s if self.wall_time_s > 0 else 0

    def add(self, result: RequestResult) -> None:
        self.total_requests += 1
        self.latencies_ms.append(result.latency_ms)
        self.status_codes[result.status] = self.status_codes.get(result.status, 0) + 1
        if result.success:
            self.successes += 1
        elif result.error:
            self.errors += 1
            if len(self.error_messages) < 5:
                self.error_messages.append(result.error[:120])
        else:
            self.failures += 1


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def timed_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    data: dict | None = None,
    expected_statuses: set[int] | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
) -> RequestResult:
    """Execute a single HTTP request with timing."""
    if expected_statuses is None:
        expected_statuses = {200}
    endpoint = url.split("/api/")[-1] if "/api/" in url else url.split("/")[-1]

    start = time.monotonic()
    try:
        kwargs: dict[str, Any] = {"timeout": timeout or REQUEST_TIMEOUT}
        if json_body is not None:
            kwargs["json"] = json_body
        if data is not None:
            kwargs["data"] = data

        async with session.request(method, url, **kwargs) as resp:
            await resp.read()  # consume body
            latency = (time.monotonic() - start) * 1000
            return RequestResult(
                endpoint=endpoint,
                method=method,
                status=resp.status,
                latency_ms=round(latency, 1),
                success=resp.status in expected_statuses,
                error=None if resp.status in expected_statuses else f"HTTP {resp.status}",
            )
    except asyncio.TimeoutError:
        latency = (time.monotonic() - start) * 1000
        return RequestResult(
            endpoint=endpoint, method=method, status=0,
            latency_ms=round(latency, 1), success=False, error="TIMEOUT",
        )
    except aiohttp.ClientError as e:
        latency = (time.monotonic() - start) * 1000
        return RequestResult(
            endpoint=endpoint, method=method, status=0,
            latency_ms=round(latency, 1), success=False, error=str(e)[:100],
        )
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return RequestResult(
            endpoint=endpoint, method=method, status=0,
            latency_ms=round(latency, 1), success=False, error=f"{type(e).__name__}: {e}"[:100],
        )


# ---------------------------------------------------------------------------
# Test categories
# ---------------------------------------------------------------------------

async def test_health_rapid_fire(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> TestResult:
    """T1: Rapid-fire health endpoint — tests basic server stability."""
    result = TestResult(name="Health Endpoint (rapid fire)")
    n = concurrency * 3  # health should handle much higher throughput

    start = time.monotonic()
    tasks = [
        timed_request(session, "GET", f"{server}/health")
        for _ in range(n)
    ]
    results = await asyncio.gather(*tasks)
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result


async def test_concurrent_task_creation(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> tuple[TestResult, list[str]]:
    """T2: Concurrent T2V task submissions — tests Redis write throughput."""
    result = TestResult(name="Concurrent Task Creation (T2V)")
    task_ids: list[str] = []

    prompts = [
        f"stress test scene {i}: a cinematic shot of nature, 4k, smooth motion"
        for i in range(concurrency)
    ]

    async def create_one(prompt: str) -> RequestResult:
        url = f"{server}/api/v1/generate"
        payload = {"prompt": prompt, "mode": "t2v", "model": "a14b"}

        start_t = time.monotonic()
        try:
            async with session.post(url, json=payload, timeout=REQUEST_TIMEOUT) as resp:
                body = await resp.json()
                latency = (time.monotonic() - start_t) * 1000
                if resp.status == 200 and "task_id" in body:
                    task_ids.append(body["task_id"])
                return RequestResult(
                    endpoint="v1/generate",
                    method="POST",
                    status=resp.status,
                    latency_ms=round(latency, 1),
                    success=resp.status == 200 and "task_id" in body,
                    error=None if resp.status == 200 else f"HTTP {resp.status}: {str(body)[:80]}",
                )
        except Exception as e:
            latency = (time.monotonic() - start_t) * 1000
            return RequestResult(
                endpoint="v1/generate", method="POST", status=0,
                latency_ms=round(latency, 1), success=False, error=str(e)[:100],
            )

    start = time.monotonic()
    results = await asyncio.gather(*[create_one(p) for p in prompts])
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result, task_ids


async def test_concurrent_task_retrieval(
    session: aiohttp.ClientSession, server: str, task_ids: list[str], concurrency: int,
) -> TestResult:
    """T3: Concurrent task status reads — tests Redis read throughput."""
    result = TestResult(name="Concurrent Task Retrieval (GET)")

    if not task_ids:
        result.error_messages.append("No task IDs available — skipped")
        return result

    # Each task ID is queried multiple times to increase load
    targets = (task_ids * ((concurrency * 2 // len(task_ids)) + 1))[:concurrency * 2]

    start = time.monotonic()
    tasks = [
        timed_request(session, "GET", f"{server}/api/v1/tasks/{tid}")
        for tid in targets
    ]
    results = await asyncio.gather(*tasks)
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result


async def test_concurrent_task_cancellation(
    session: aiohttp.ClientSession, server: str, task_ids: list[str],
) -> TestResult:
    """T4: Concurrent task cancellation — tests Redis write consistency."""
    result = TestResult(name="Concurrent Task Cancellation")

    if not task_ids:
        result.error_messages.append("No task IDs available — skipped")
        return result

    start = time.monotonic()
    tasks = [
        timed_request(
            session, "POST", f"{server}/api/v1/tasks/{tid}/cancel",
            expected_statuses={200, 409},  # 409 = already cancelled / not queued
        )
        for tid in task_ids
    ]
    results = await asyncio.gather(*tasks)
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result


async def test_lora_catalog_reads(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> TestResult:
    """T5: Concurrent LoRA catalog reads — tests static data serving."""
    result = TestResult(name="LoRA Catalog Reads")

    start = time.monotonic()
    tasks = [
        timed_request(session, "GET", f"{server}/api/v1/loras")
        for _ in range(concurrency)
    ]
    results = await asyncio.gather(*tasks)
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result


async def test_preset_reads(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> TestResult:
    """T6: Concurrent model/T5 preset reads."""
    result = TestResult(name="Model & T5 Preset Reads")

    start = time.monotonic()
    tasks = []
    for i in range(concurrency):
        if i % 2 == 0:
            tasks.append(timed_request(session, "GET", f"{server}/api/v1/model-presets"))
        else:
            tasks.append(timed_request(session, "GET", f"{server}/api/v1/t5-presets"))
    results = await asyncio.gather(*tasks)
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result


async def test_mixed_workload(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> TestResult:
    """T7: Mixed read/write workload — simulates real usage patterns."""
    result = TestResult(name="Mixed Workload (read + write)")

    async def mixed_operation(idx: int) -> list[RequestResult]:
        results_batch: list[RequestResult] = []

        # 1. Health check
        results_batch.append(
            await timed_request(session, "GET", f"{server}/health")
        )

        # 2. List tasks
        results_batch.append(
            await timed_request(session, "GET", f"{server}/api/v1/tasks")
        )

        # 3. Create a task
        payload = {"prompt": f"mixed workload scene {idx}", "mode": "t2v", "model": "5b"}

        start_t = time.monotonic()
        try:
            async with session.post(
                f"{server}/api/v1/generate", json=payload, timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.json()
                latency = (time.monotonic() - start_t) * 1000
                task_id = body.get("task_id", "")
                results_batch.append(RequestResult(
                    endpoint="v1/generate", method="POST", status=resp.status,
                    latency_ms=round(latency, 1), success=resp.status == 200,
                ))
        except Exception as e:
            latency = (time.monotonic() - start_t) * 1000
            results_batch.append(RequestResult(
                endpoint="v1/generate", method="POST", status=0,
                latency_ms=round(latency, 1), success=False, error=str(e)[:80],
            ))
            task_id = ""

        # 4. Read back the task
        if task_id:
            results_batch.append(
                await timed_request(session, "GET", f"{server}/api/v1/tasks/{task_id}")
            )
            # 5. Cancel it
            results_batch.append(
                await timed_request(
                    session, "POST", f"{server}/api/v1/tasks/{task_id}/cancel",
                    expected_statuses={200, 409},
                )
            )

        return results_batch

    start = time.monotonic()
    all_batches = await asyncio.gather(*[mixed_operation(i) for i in range(concurrency)])
    result.wall_time_s = time.monotonic() - start

    for batch in all_batches:
        for r in batch:
            result.add(r)
    return result


async def test_connection_exhaustion(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> TestResult:
    """T8: High connection count — tests server connection limits."""
    result = TestResult(name="Connection Exhaustion")
    n = concurrency * 5  # much higher than normal

    start = time.monotonic()
    tasks = [
        timed_request(session, "GET", f"{server}/health", timeout=CONNECT_TIMEOUT)
        for _ in range(n)
    ]
    results = await asyncio.gather(*tasks)
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result


async def test_error_handling_under_load(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> TestResult:
    """T9: Invalid requests under load — tests error path stability."""
    result = TestResult(name="Error Handling (invalid requests)")

    async def bad_request(idx: int) -> RequestResult:
        variant = idx % 5
        if variant == 0:
            # Missing prompt
            return await timed_request(
                session, "POST", f"{server}/api/v1/generate",
                json_body={"mode": "t2v", "model": "a14b"},
                expected_statuses={400, 422},
            )
        elif variant == 1:
            # Invalid model
            return await timed_request(
                session, "POST", f"{server}/api/v1/generate",
                json_body={"prompt": "test", "mode": "t2v", "model": "nonexistent"},
                expected_statuses={400, 422},
            )
        elif variant == 2:
            # Nonexistent task
            return await timed_request(
                session, "GET", f"{server}/api/v1/tasks/fake-task-{idx}",
                expected_statuses={404},
            )
        elif variant == 3:
            # Cancel nonexistent task
            return await timed_request(
                session, "POST", f"{server}/api/v1/tasks/fake-task-{idx}/cancel",
                expected_statuses={404, 409},
            )
        else:
            # i2v without image
            return await timed_request(
                session, "POST", f"{server}/api/v1/generate",
                json_body={"prompt": "test", "mode": "i2v", "model": "a14b"},
                expected_statuses={400, 422},
            )

    start = time.monotonic()
    tasks = [bad_request(i) for i in range(concurrency)]
    results = await asyncio.gather(*tasks)
    result.wall_time_s = time.monotonic() - start

    for r in results:
        result.add(r)
    return result


async def test_redis_task_lifecycle(
    session: aiohttp.ClientSession, server: str, concurrency: int,
) -> TestResult:
    """T10: Rapid create-read-cancel cycles — tests Redis transaction integrity."""
    result = TestResult(name="Redis Task Lifecycle (create-read-cancel)")

    async def lifecycle(idx: int) -> list[RequestResult]:
        batch: list[RequestResult] = []

        # Create
        payload = {"prompt": f"lifecycle test {idx}", "mode": "t2v", "model": "a14b"}

        start_t = time.monotonic()
        task_id = ""
        try:
            async with session.post(
                f"{server}/api/v1/generate", json=payload, timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.json()
                latency = (time.monotonic() - start_t) * 1000
                task_id = body.get("task_id", "")
                batch.append(RequestResult(
                    endpoint="create", method="POST", status=resp.status,
                    latency_ms=round(latency, 1), success=resp.status == 200 and bool(task_id),
                ))
        except Exception as e:
            latency = (time.monotonic() - start_t) * 1000
            batch.append(RequestResult(
                endpoint="create", method="POST", status=0,
                latency_ms=round(latency, 1), success=False, error=str(e)[:80],
            ))

        if not task_id:
            return batch

        # Read (should be queued)
        r = await timed_request(session, "GET", f"{server}/api/v1/tasks/{task_id}")
        batch.append(r)

        # Cancel
        r = await timed_request(
            session, "POST", f"{server}/api/v1/tasks/{task_id}/cancel",
            expected_statuses={200, 409},
        )
        batch.append(r)

        # Read again (should be cancelled)
        r = await timed_request(session, "GET", f"{server}/api/v1/tasks/{task_id}")
        batch.append(r)

        return batch

    start = time.monotonic()
    all_batches = await asyncio.gather(*[lifecycle(i) for i in range(concurrency)])
    result.wall_time_s = time.monotonic() - start

    for batch in all_batches:
        for r in batch:
            result.add(r)
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_result(tr: TestResult) -> None:
    """Print a single test result."""
    status = "PASS" if tr.success_rate >= 95 else "WARN" if tr.success_rate >= 80 else "FAIL"
    icon = {"PASS": "+", "WARN": "~", "FAIL": "!"}[status]

    print(f"\n  [{icon}] {tr.name}")
    print(f"      Requests:     {tr.total_requests}")
    print(f"      Success rate: {tr.success_rate:.1f}% ({tr.successes}/{tr.total_requests})")
    print(f"      Throughput:   {tr.rps:.1f} req/s")
    print(f"      Latency:      avg={tr.avg_ms:.0f}ms  p50={tr.p50_ms:.0f}ms  p95={tr.p95_ms:.0f}ms  p99={tr.p99_ms:.0f}ms  max={tr.max_ms:.0f}ms")
    print(f"      Wall time:    {tr.wall_time_s:.2f}s")

    if tr.status_codes:
        codes_str = "  ".join(f"{code}:{count}" for code, count in sorted(tr.status_codes.items()))
        print(f"      Status codes: {codes_str}")

    if tr.error_messages:
        print(f"      Errors (first {len(tr.error_messages)}):")
        for msg in tr.error_messages:
            print(f"        - {msg}")


def print_summary(all_results: list[TestResult]) -> None:
    """Print final summary."""
    total_reqs = sum(tr.total_requests for tr in all_results)
    total_success = sum(tr.successes for tr in all_results)
    total_wall = sum(tr.wall_time_s for tr in all_results)
    overall_rate = (total_success / total_reqs * 100) if total_reqs > 0 else 0

    all_latencies = []
    for tr in all_results:
        all_latencies.extend(tr.latencies_ms)

    print(f"\n{'='*70}")
    print(f"  OVERALL SUMMARY")
    print(f"{'='*70}")
    print(f"  Total requests:     {total_reqs}")
    print(f"  Total successes:    {total_success}")
    print(f"  Overall success:    {overall_rate:.1f}%")
    print(f"  Total wall time:    {total_wall:.1f}s")
    if all_latencies:
        print(f"  Global avg latency: {statistics.mean(all_latencies):.0f}ms")
        sorted_lat = sorted(all_latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        print(f"  Global p95 latency: {sorted_lat[min(p95_idx, len(sorted_lat)-1)]:.0f}ms")
    print()

    passed = sum(1 for tr in all_results if tr.success_rate >= 95)
    warned = sum(1 for tr in all_results if 80 <= tr.success_rate < 95)
    failed = sum(1 for tr in all_results if tr.success_rate < 80)

    print(f"  Tests: {passed} PASS / {warned} WARN / {failed} FAIL")

    if overall_rate >= 95:
        print(f"\n  VERDICT: SERVICE IS RELIABLE under tested load")
    elif overall_rate >= 80:
        print(f"\n  VERDICT: SERVICE HAS MINOR ISSUES under load")
    else:
        print(f"\n  VERDICT: SERVICE HAS RELIABILITY PROBLEMS")

    print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_all_tests(server: str, concurrency: int, api_key: str = DEFAULT_API_KEY) -> list[TestResult]:
    """Run all stress test categories sequentially."""
    print(f"\n{'='*70}")
    print(f"  Wan2.2 Service Stress Test")
    print(f"  Server:      {server}")
    print(f"  Concurrency: {concurrency}")
    print(f"  API Key:     {api_key[:8]}...{api_key[-4:]}")
    print(f"  Time:        {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    connector = aiohttp.TCPConnector(
        limit=concurrency * 6,  # allow high connection count
        limit_per_host=concurrency * 6,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    headers = {"X-API-Key": api_key}

    all_results: list[TestResult] = []

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        # T1: Health rapid fire
        print("\n[T1] Health endpoint rapid fire...")
        tr = await test_health_rapid_fire(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)

        # T2: Concurrent task creation
        print("\n[T2] Concurrent task creation...")
        tr, task_ids = await test_concurrent_task_creation(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)
        print(f"      Created {len(task_ids)} tasks for subsequent tests")

        # T3: Concurrent task retrieval
        print("\n[T3] Concurrent task retrieval...")
        tr = await test_concurrent_task_retrieval(session, server, task_ids, concurrency)
        all_results.append(tr)
        print_result(tr)

        # T4: Concurrent task cancellation
        print("\n[T4] Concurrent task cancellation...")
        tr = await test_concurrent_task_cancellation(session, server, task_ids)
        all_results.append(tr)
        print_result(tr)

        # T5: LoRA catalog reads
        print("\n[T5] LoRA catalog reads under load...")
        tr = await test_lora_catalog_reads(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)

        # T6: Model/T5 preset reads
        print("\n[T6] Model & T5 preset reads...")
        tr = await test_preset_reads(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)

        # T7: Mixed workload
        print("\n[T7] Mixed read/write workload...")
        tr = await test_mixed_workload(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)

        # T8: Connection exhaustion
        print("\n[T8] Connection exhaustion test...")
        tr = await test_connection_exhaustion(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)

        # T9: Error handling under load
        print("\n[T9] Error handling (invalid requests)...")
        tr = await test_error_handling_under_load(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)

        # T10: Redis task lifecycle
        print("\n[T10] Redis task lifecycle (create-read-cancel)...")
        tr = await test_redis_task_lifecycle(session, server, concurrency)
        all_results.append(tr)
        print_result(tr)

    # Summary
    print_summary(all_results)
    return all_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Wan2.2 Service Stress Test")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"API server URL (default: {DEFAULT_SERVER})")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="X-API-Key header value")
    parser.add_argument("--concurrency", "-c", type=int, default=DEFAULT_CONCURRENCY, help=f"Concurrent requests per test (default: {DEFAULT_CONCURRENCY})")
    parser.add_argument("--quick", action="store_true", help=f"Quick smoke test (concurrency={QUICK_CONCURRENCY})")
    args = parser.parse_args()

    if args.quick:
        args.concurrency = QUICK_CONCURRENCY

    asyncio.run(run_all_tests(args.server, args.concurrency, args.api_key))


if __name__ == "__main__":
    main()
