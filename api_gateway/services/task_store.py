"""TaskStore — MySQL-backed persistent task storage.

All MySQL operations are best-effort: errors are logged but never raised,
so they cannot block or break Redis-based task flow.
"""

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

from api_gateway.config import GatewayConfig

logger = logging.getLogger(__name__)

# Terminal statuses that trigger completed_at
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


class TaskStore:
    """MySQL-backed persistent task storage.

    Every public method is async and wraps synchronous pymysql calls via
    ``asyncio.to_thread`` so the event loop is never blocked.

    All methods swallow exceptions internally — callers never need
    try/except around TaskStore calls.
    """

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self):
        """Create a new pymysql connection (synchronous)."""
        import pymysql
        import pymysql.cursors

        return pymysql.connect(
            host=self._config.mysql_host,
            port=self._config.mysql_port,
            user=self._config.mysql_user,
            password=self._config.mysql_password,
            database=self._config.mysql_db,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
        )

    def _exec(self, sql: str, args: tuple | None = None) -> list[dict]:
        """Execute a single SQL statement synchronously and return rows."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def _exec_write(self, sql: str, args: tuple | None = None) -> int:
        """Execute a write SQL statement synchronously and return rowcount."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                affected = cur.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        task_id: str,
        task_type: str,
        category: str = "local",
        provider: Optional[str] = None,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        params: Optional[dict] = None,
        parent_task_id: Optional[str] = None,
        chain_id: Optional[str] = None,
        external_task_id: Optional[str] = None,
    ) -> None:
        """Insert a new task row. Best-effort -- logs errors, never raises."""
        try:
            params_json = json.dumps(params) if params is not None else None
            sql = (
                "INSERT IGNORE INTO generation_tasks "
                "(task_id, task_type, category, provider, prompt, model, "
                "params_json, parent_task_id, chain_id, external_task_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            )
            args = (
                task_id, task_type, category, provider, prompt, model,
                params_json, parent_task_id, chain_id, external_task_id,
            )
            await asyncio.to_thread(self._exec_write, sql, args)
            logger.debug("TaskStore.create: task_id=%s type=%s category=%s", task_id, task_type, category)
        except Exception:
            logger.warning("TaskStore.create failed for task %s", task_id, exc_info=True)

    async def update_status(
        self,
        task_id: str,
        status: str,
        *,
        progress: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update task status. Sets started_at when running, completed_at when terminal."""
        try:
            set_parts = ["status = %s"]
            args: list[Any] = [status]

            if progress is not None:
                set_parts.append("progress = %s")
                args.append(progress)

            if error is not None:
                set_parts.append("error_message = %s")
                args.append(error)

            if status == "running":
                set_parts.append("started_at = COALESCE(started_at, NOW(3))")

            if status in _TERMINAL_STATUSES:
                set_parts.append("completed_at = COALESCE(completed_at, NOW(3))")

            sql = f"UPDATE generation_tasks SET {', '.join(set_parts)} WHERE task_id = %s"
            args.append(task_id)
            await asyncio.to_thread(self._exec_write, sql, tuple(args))
            logger.debug("TaskStore.update_status: task_id=%s status=%s", task_id, status)
        except Exception:
            logger.warning("TaskStore.update_status failed for task %s", task_id, exc_info=True)

    async def set_result(
        self,
        task_id: str,
        *,
        result_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        extra_urls: Optional[list[str]] = None,
    ) -> None:
        """Set result URLs for a completed task."""
        try:
            set_parts: list[str] = []
            args: list[Any] = []

            if result_url is not None:
                set_parts.append("result_url = %s")
                args.append(result_url)

            if thumbnail_url is not None:
                set_parts.append("thumbnail_url = %s")
                args.append(thumbnail_url)

            if extra_urls is not None:
                set_parts.append("extra_urls = %s")
                args.append(json.dumps(extra_urls))

            if not set_parts:
                return

            sql = f"UPDATE generation_tasks SET {', '.join(set_parts)} WHERE task_id = %s"
            args.append(task_id)
            await asyncio.to_thread(self._exec_write, sql, tuple(args))
            logger.debug("TaskStore.set_result: task_id=%s", task_id)
        except Exception:
            logger.warning("TaskStore.set_result failed for task %s", task_id, exc_info=True)

    async def get(self, task_id: str) -> Optional[dict]:
        """Fetch a single task by task_id."""
        try:
            sql = "SELECT * FROM generation_tasks WHERE task_id = %s"
            rows = await asyncio.to_thread(self._exec, sql, (task_id,))
            if not rows:
                return None
            return _row_to_dict(rows[0])
        except Exception:
            logger.warning("TaskStore.get failed for task %s", task_id, exc_info=True)
            return None

    async def list_history(
        self,
        *,
        category: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 24,
    ) -> dict:
        """Query tasks with filters and pagination.

        Returns::

            {
                "tasks": [...],
                "total": int,
                "total_pages": int,
                "page": int,
                "page_size": int,
                "category_counts": {"local": N, "thirdparty": N, ...},
            }
        """
        try:
            where_parts: list[str] = []
            args: list[Any] = []

            if category:
                where_parts.append("category = %s")
                args.append(category)

            if status:
                where_parts.append("status = %s")
                args.append(status)

            if q:
                where_parts.append("(prompt LIKE %s OR task_id LIKE %s)")
                pattern = f"%{q}%"
                args.extend([pattern, pattern])

            where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

            # Total count
            count_sql = f"SELECT COUNT(*) AS cnt FROM generation_tasks {where_clause}"
            count_rows = await asyncio.to_thread(self._exec, count_sql, tuple(args) if args else None)
            total = count_rows[0]["cnt"] if count_rows else 0

            # Category counts (always unfiltered by category for sidebar display)
            cat_where_parts: list[str] = []
            cat_args: list[Any] = []
            if status:
                cat_where_parts.append("status = %s")
                cat_args.append(status)
            if q:
                cat_where_parts.append("(prompt LIKE %s OR task_id LIKE %s)")
                pattern = f"%{q}%"
                cat_args.extend([pattern, pattern])
            cat_where = f"WHERE {' AND '.join(cat_where_parts)}" if cat_where_parts else ""
            cat_sql = f"SELECT category, COUNT(*) AS cnt FROM generation_tasks {cat_where} GROUP BY category"
            cat_rows = await asyncio.to_thread(
                self._exec, cat_sql, tuple(cat_args) if cat_args else None,
            )
            category_counts = {row["category"]: row["cnt"] for row in cat_rows}

            # Paginated results
            total_pages = max(1, math.ceil(total / page_size))
            offset = (page - 1) * page_size
            data_sql = (
                f"SELECT * FROM generation_tasks {where_clause} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s"
            )
            data_args = list(args) + [page_size, offset]
            rows = await asyncio.to_thread(self._exec, data_sql, tuple(data_args))
            tasks = [_row_to_dict(row) for row in rows]

            return {
                "tasks": tasks,
                "total": total,
                "total_pages": total_pages,
                "page": page,
                "page_size": page_size,
                "category_counts": category_counts,
            }
        except Exception:
            logger.warning("TaskStore.list_history failed", exc_info=True)
            return {
                "tasks": [],
                "total": 0,
                "total_pages": 1,
                "page": page,
                "page_size": page_size,
                "category_counts": {},
            }


def _row_to_dict(row: dict) -> dict:
    """Convert a MySQL row dict to the format expected by the frontend.

    Key mappings:
    - ``task_id`` → also set as ``workflow_id`` (frontend compat)
    - ``task_type`` → also set as ``mode`` (frontend compat)
    - ``result_url`` → also set as ``final_video_url``
    - ``prompt`` → also set as ``user_prompt``
    - ``error_message`` → also set as ``error``
    - ``created_at`` → unix timestamp (float)
    """
    result = dict(row)

    # Parse JSON fields
    if result.get("params_json"):
        try:
            result["params"] = json.loads(result["params_json"])
        except (json.JSONDecodeError, TypeError):
            result["params"] = None
    else:
        result["params"] = None
    result.pop("params_json", None)

    if result.get("extra_urls"):
        try:
            extra = json.loads(result["extra_urls"])
            result["extra_urls"] = extra
            # Promote common extra URLs for frontend compat
            if isinstance(extra, dict):
                if not result.get("thumbnail_url") and extra.get("first_frame_url"):
                    result["thumbnail_url"] = extra["first_frame_url"]
                result.setdefault("first_frame_url", extra.get("first_frame_url"))
                result.setdefault("edited_frame_url", extra.get("edited_frame_url"))
        except (json.JSONDecodeError, TypeError):
            result["extra_urls"] = None

    # Convert datetime objects to unix timestamps (frontend expects numbers)
    for dt_field in ("created_at", "started_at", "completed_at"):
        val = result.get(dt_field)
        if isinstance(val, datetime):
            result[dt_field] = val.replace(tzinfo=timezone.utc).timestamp()

    # Frontend compat aliases
    result["workflow_id"] = result.get("task_id", "")
    result["mode"] = result.get("task_type", "")
    result["user_prompt"] = result.get("prompt", "")
    result["final_video_url"] = result.get("result_url")
    result["error"] = result.get("error_message")
    result.setdefault("first_frame_url", None)
    result.setdefault("edited_frame_url", None)

    return result
