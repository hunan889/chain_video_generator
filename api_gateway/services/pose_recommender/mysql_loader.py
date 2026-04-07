"""Load enabled poses from the MySQL ``poses`` table.

Replaces the old monolith's sqlite loader. Returns plain dicts so the
recommender stays decoupled from the DB layer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import pymysql
import pymysql.cursors

from api_gateway.config import GatewayConfig
from api_gateway.services.pose_recommender.synonyms import get_synonyms

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoseMeta:
    """One row from the poses table, enriched with synonyms.

    ``search_text`` is the canonical string we feed into the embedding model
    so semantic matches don't have to fight Chinese-only display names.
    """

    pose_id: int
    pose_key: str
    name_en: str
    name_cn: str
    category: str
    description: str
    search_text: str


def _get_connection(config: GatewayConfig) -> pymysql.connections.Connection:
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


def _build_search_text(row: dict) -> str:
    """Concatenate name, key tokens, description, and synonyms.

    The result is what gets embedded by BGE. Including synonyms here means
    a single embedding per pose covers many natural-language variants.
    """
    parts: list[str] = []
    if row.get("name_en"):
        parts.append(row["name_en"])
    if row.get("pose_key"):
        parts.append(row["pose_key"].replace("_", " "))
    if row.get("description"):
        parts.append(row["description"])
    parts.extend(get_synonyms(row.get("pose_key", "")))
    return " ".join(p for p in parts if p).strip()


def _load_poses_sync(config: GatewayConfig) -> list[PoseMeta]:
    conn = _get_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, pose_key, name_cn, name_en, category, description "
                "FROM poses WHERE enabled = 1 ORDER BY id"
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()

    poses: list[PoseMeta] = []
    for row in rows:
        poses.append(PoseMeta(
            pose_id=int(row["id"]),
            pose_key=row.get("pose_key") or "",
            name_en=row.get("name_en") or "",
            name_cn=row.get("name_cn") or "",
            category=row.get("category") or "",
            description=row.get("description") or "",
            search_text=_build_search_text(row),
        ))
    logger.info("Loaded %d enabled poses from MySQL", len(poses))
    return poses


async def load_enabled_poses(config: GatewayConfig) -> list[PoseMeta]:
    """Async wrapper around the sync MySQL load."""
    return await asyncio.to_thread(_load_poses_sync, config)
