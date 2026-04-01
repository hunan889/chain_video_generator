"""Lightweight LoRA search service using MySQL keyword matching.

Replaces the heavy embedding-based service (SentenceTransformer + Zilliz)
with simple SQL LIKE queries against lora_metadata / image_lora_metadata.
"""

import asyncio
import json
import logging
from typing import Any

import pymysql
import pymysql.cursors

from api_gateway.config import GatewayConfig

logger = logging.getLogger(__name__)


def _parse_json_field(raw: Any) -> list:
    """Safely parse a JSON string field into a list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            return [raw] if raw.strip() else []
    return []


def _get_connection(config: GatewayConfig) -> pymysql.connections.Connection:
    """Create a fresh MySQL connection with DictCursor."""
    return pymysql.connect(
        host=config.mysql_host,
        port=config.mysql_port,
        user=config.mysql_user,
        password=config.mysql_password,
        database=config.mysql_db,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


class LoraSearchService:
    """Simplified LoRA search using MySQL keyword matching.

    Queries ``lora_metadata`` (video) and ``image_lora_metadata`` (image) tables
    using LIKE-based keyword matching on name, description, and trigger_words.
    """

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Video LoRA search
    # ------------------------------------------------------------------

    def _search_video_loras_sync(
        self, prompt: str, mode: str, top_k: int
    ) -> list[dict]:
        """Synchronous keyword search against lora_metadata."""
        keywords = [w.strip().lower() for w in prompt.split() if len(w.strip()) >= 2]
        if not keywords:
            return []

        # Build OR conditions for keyword matching
        conditions: list[str] = []
        params: list[str] = []
        for kw in keywords[:10]:  # limit to first 10 keywords
            pattern = f"%{kw}%"
            conditions.append(
                "(LOWER(name) LIKE %s OR LOWER(description) LIKE %s "
                "OR LOWER(trigger_words) LIKE %s)"
            )
            params.extend([pattern, pattern, pattern])

        where_clause = " OR ".join(conditions)

        # Mode filter: T2V or I2V
        mode_filter = "T2V" if mode in (None, "t2v") else "I2V"

        query = (
            "SELECT id, name, mode, trigger_words, trigger_prompt, "
            "noise_stage, description, preview_url, example_prompts "
            f"FROM lora_metadata WHERE (enabled = 1) AND mode = %s AND ({where_clause}) "
            f"ORDER BY id ASC LIMIT %s"
        )
        params_final = [mode_filter] + params + [top_k * 3]

        conn = _get_connection(self._config)
        try:
            with conn.cursor() as cur:
                cur.execute(query, params_final)
                rows = cur.fetchall()
        finally:
            conn.close()

        # Score rows by number of keyword hits
        scored: list[tuple[int, dict]] = []
        for row in rows:
            searchable = " ".join(
                str(row.get(f, "") or "").lower()
                for f in ("name", "description", "trigger_words")
            )
            hits = sum(1 for kw in keywords if kw in searchable)
            scored.append((hits, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[dict] = []
        for _score, row in scored[:top_k]:
            results.append({
                "lora_id": row["id"],
                "name": row.get("name", ""),
                "mode": row.get("mode", ""),
                "trigger_words": _parse_json_field(row.get("trigger_words")),
                "trigger_prompt": row.get("trigger_prompt") or None,
                "noise_stage": row.get("noise_stage"),
                "description": row.get("description") or "",
                "preview_url": row.get("preview_url") or None,
                "example_prompts": _parse_json_field(row.get("example_prompts")),
                "similarity": round(_score / max(len(keywords), 1), 3),
            })
        return results

    async def search_video_loras(
        self, prompt: str, mode: str = "t2v", top_k: int = 5
    ) -> list[dict]:
        """Search lora_metadata by keyword matching on name, description, trigger_words.

        Args:
            prompt: User prompt text.
            mode: "t2v" or "i2v" (determines whether to search T2V or I2V loras).
            top_k: Maximum number of results.

        Returns:
            List of LoRA dicts with keys: lora_id, name, mode, trigger_words,
            trigger_prompt, noise_stage, description, preview_url, similarity.
        """
        return await asyncio.to_thread(
            self._search_video_loras_sync, prompt, mode, top_k
        )

    # ------------------------------------------------------------------
    # Image LoRA search
    # ------------------------------------------------------------------

    def _search_image_loras_sync(self, prompt: str, top_k: int) -> list[dict]:
        """Synchronous keyword search against image_lora_metadata."""
        keywords = [w.strip().lower() for w in prompt.split() if len(w.strip()) >= 2]
        if not keywords:
            return []

        conditions: list[str] = []
        params: list[str] = []
        for kw in keywords[:10]:
            pattern = f"%{kw}%"
            conditions.append(
                "(LOWER(name) LIKE %s OR LOWER(description) LIKE %s "
                "OR LOWER(trigger_prompt) LIKE %s OR LOWER(tags) LIKE %s)"
            )
            params.extend([pattern, pattern, pattern, pattern])

        where_clause = " OR ".join(conditions)

        query = (
            "SELECT id, name, trigger_prompt, trigger_words, tags, "
            "description, preview_url, weight "
            f"FROM image_lora_metadata WHERE (enabled = 1) AND ({where_clause}) "
            f"ORDER BY id ASC LIMIT %s"
        )
        params_final = params + [top_k * 3]

        conn = _get_connection(self._config)
        try:
            with conn.cursor() as cur:
                cur.execute(query, params_final)
                rows = cur.fetchall()
        finally:
            conn.close()

        scored: list[tuple[int, dict]] = []
        for row in rows:
            searchable = " ".join(
                str(row.get(f, "") or "").lower()
                for f in ("name", "description", "trigger_prompt", "tags")
            )
            hits = sum(1 for kw in keywords if kw in searchable)
            scored.append((hits, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[dict] = []
        for _score, row in scored[:top_k]:
            results.append({
                "lora_id": row["id"],
                "name": row.get("name", ""),
                "trigger_prompt": row.get("trigger_prompt") or None,
                "trigger_words": _parse_json_field(row.get("trigger_words")),
                "tags": _parse_json_field(row.get("tags")),
                "description": row.get("description") or "",
                "preview_url": row.get("preview_url") or None,
                "weight": float(row.get("weight", 0.8) or 0.8),
                "similarity": round(_score / max(len(keywords), 1), 3),
            })
        return results

    async def search_image_loras(
        self, prompt: str, top_k: int = 5
    ) -> list[dict]:
        """Search image_lora_metadata by keyword matching.

        Args:
            prompt: User prompt text.
            top_k: Maximum number of results.

        Returns:
            List of image LoRA dicts with keys: lora_id, name, trigger_prompt,
            trigger_words, tags, description, preview_url, weight, similarity.
        """
        return await asyncio.to_thread(
            self._search_image_loras_sync, prompt, top_k
        )
