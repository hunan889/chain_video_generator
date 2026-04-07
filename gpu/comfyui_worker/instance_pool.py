"""Dynamic ComfyUI instance pool with health tracking and failover.

Replaces static single-URL-per-model config with a pool that:
- Tracks health per instance (consecutive failures, cooldown)
- Round-robins across healthy instances
- Auto-discovers new instances from Redis registry
- Recovers instances after cooldown expires
"""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Defaults — overridden by WorkerConfig values when available
FAILURE_THRESHOLD = 3       # consecutive failures before cooldown
COOLDOWN_BASE_S = 30        # initial cooldown duration
COOLDOWN_MAX_S = 300        # max cooldown (5 min)


@dataclass
class InstanceState:
    url: str
    model_key: str
    healthy: bool = True
    consecutive_failures: int = 0
    last_success: float = 0.0
    cooldown_until: float = 0.0  # unix timestamp; 0 = not in cooldown

    @property
    def in_cooldown(self) -> bool:
        return self.cooldown_until > time.time()

    @property
    def available(self) -> bool:
        return self.healthy and not self.in_cooldown


class InstancePool:
    """Manages ComfyUI instances per model key with health tracking."""

    def __init__(
        self,
        static_urls: dict[str, str],
        redis=None,
        failure_threshold: int = FAILURE_THRESHOLD,
        cooldown_base: float = COOLDOWN_BASE_S,
        cooldown_max: float = COOLDOWN_MAX_S,
    ):
        self._redis = redis
        self._failure_threshold = failure_threshold
        self._cooldown_base = cooldown_base
        self._cooldown_max = cooldown_max

        # model_key -> list[InstanceState]
        self._instances: dict[str, list[InstanceState]] = {}
        # model_key -> round-robin index
        self._rr_index: dict[str, int] = {}

        # Seed from static config
        for model_key, url in static_urls.items():
            self._add_instance(model_key, url)

        logger.info(
            "InstancePool initialized: %s",
            {k: [i.url for i in v] for k, v in self._instances.items()},
        )

    def _add_instance(self, model_key: str, url: str) -> None:
        """Add an instance if not already tracked."""
        url = url.rstrip("/")
        if model_key not in self._instances:
            self._instances[model_key] = []
            self._rr_index[model_key] = 0
        existing_urls = {i.url for i in self._instances[model_key]}
        if url not in existing_urls:
            self._instances[model_key].append(
                InstanceState(url=url, model_key=model_key)
            )

    def get_instance(self, model_key: str) -> str | None:
        """Pick a healthy instance via round-robin. Falls back to least-stale."""
        instances = self._instances.get(model_key, [])
        if not instances:
            return None

        # Expire cooldowns
        now = time.time()
        for inst in instances:
            if inst.cooldown_until and inst.cooldown_until <= now:
                inst.cooldown_until = 0.0
                # Don't mark healthy yet — let health checker confirm

        # Try round-robin among available instances
        n = len(instances)
        start = self._rr_index.get(model_key, 0) % n
        for offset in range(n):
            idx = (start + offset) % n
            inst = instances[idx]
            if inst.available:
                self._rr_index[model_key] = (idx + 1) % n
                return inst.url

        # No healthy instance — return the one whose cooldown expired earliest
        # (or with fewest failures if none are in cooldown)
        best = min(instances, key=lambda i: (i.cooldown_until, i.consecutive_failures))
        logger.warning(
            "No healthy instance for '%s', using least-stale: %s (failures=%d)",
            model_key, best.url, best.consecutive_failures,
        )
        return best.url

    def report_success(self, model_key: str, url: str) -> None:
        """Mark an instance as healthy after a successful task."""
        inst = self._find(model_key, url)
        if inst:
            inst.healthy = True
            inst.consecutive_failures = 0
            inst.last_success = time.time()
            inst.cooldown_until = 0.0

    def report_failure(self, model_key: str, url: str) -> None:
        """Record a failure. After threshold, apply exponential cooldown."""
        inst = self._find(model_key, url)
        if not inst:
            return
        inst.consecutive_failures += 1
        inst.healthy = False

        if inst.consecutive_failures >= self._failure_threshold:
            # Exponential backoff: base * 2^(failures - threshold)
            exponent = inst.consecutive_failures - self._failure_threshold
            cooldown = min(self._cooldown_base * (2 ** exponent), self._cooldown_max)
            inst.cooldown_until = time.time() + cooldown
            logger.warning(
                "Instance %s for '%s' in cooldown %.0fs (failures=%d)",
                url, model_key, cooldown, inst.consecutive_failures,
            )
        else:
            logger.info(
                "Instance %s for '%s' failure %d/%d",
                url, model_key, inst.consecutive_failures, self._failure_threshold,
            )

    def mark_healthy(self, model_key: str, url: str) -> None:
        """Called by health checker when probe succeeds."""
        inst = self._find(model_key, url)
        if inst and not inst.healthy:
            logger.info("Instance %s for '%s' recovered", url, model_key)
            inst.healthy = True
            inst.consecutive_failures = 0
            inst.cooldown_until = 0.0

    def mark_unhealthy(self, model_key: str, url: str) -> None:
        """Called by health checker when probe fails."""
        inst = self._find(model_key, url)
        if inst and inst.healthy:
            inst.healthy = False

    async def refresh_from_registry(self) -> None:
        """Read comfyui_instances:<model> SETs from Redis and add new URLs."""
        if not self._redis:
            return
        for model_key in list(self._instances.keys()):
            try:
                urls = await self._redis.smembers(f"comfyui_instances:{model_key}")
                for url in urls:
                    if isinstance(url, bytes):
                        url = url.decode()
                    self._add_instance(model_key, url)
            except Exception as exc:
                logger.debug("Failed to refresh registry for %s: %s", model_key, exc)

    def get_all_instances(self) -> dict[str, list[InstanceState]]:
        """Return all tracked instances for health checking / reporting."""
        return dict(self._instances)

    def get_health_summary(self) -> dict:
        """Compact summary for heartbeat reporting."""
        result = {}
        for model_key, instances in self._instances.items():
            result[model_key] = [
                {
                    "url": i.url,
                    "healthy": i.healthy,
                    "failures": i.consecutive_failures,
                    "cooldown_remaining": max(0, int(i.cooldown_until - time.time())),
                }
                for i in instances
            ]
        return result

    def _find(self, model_key: str, url: str) -> InstanceState | None:
        url = url.rstrip("/")
        for inst in self._instances.get(model_key, []):
            if inst.url == url:
                return inst
        return None
