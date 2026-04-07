"""Stage 1 -- Prompt Analysis & LoRA Selection.

Ported from api/routes/workflow_executor.py (_analyze_prompt and helpers).
All MySQL access uses pymysql with %s placeholders and DictCursor,
wrapped in asyncio.to_thread() for non-blocking operation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Optional

import pymysql
import pymysql.cursors

from api_gateway.config import GatewayConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """Output of Stage 1 prompt analysis."""

    video_prompt: str
    t2i_prompt: str
    t2i_negative_prompt: str
    image_loras: list[dict] = field(default_factory=list)
    video_loras: list[dict] = field(default_factory=list)
    reference_image: str | None = None
    reference_skip_reactor: bool = False
    pose_keys: list[str] = field(default_factory=list)
    original_prompt: str = ""
    optimized_prompt: str = ""


# ---------------------------------------------------------------------------
# Quality tag constants (T2I)
# ---------------------------------------------------------------------------

T2I_POSITIVE_TAGS = (
    "masterpiece, best quality, ultra detailed, high resolution, "
    "sharp focus, realistic, photorealistic"
)

T2I_NEGATIVE_TAGS = (
    "low quality, blurry, distorted, deformed, bad anatomy, bad hands, "
    "extra fingers, ugly, cartoon, anime, painting, drawing, "
    "overexposed, oversaturated, plastic skin, airbrushed, "
    "glossy skin, shiny skin, doll, cropped, watermark, text"
)


# ---------------------------------------------------------------------------
# MySQL helper
# ---------------------------------------------------------------------------

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


def _parse_json_field(raw: Any) -> list:
    """Safely parse a JSON-encoded string into a list."""
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


# ---------------------------------------------------------------------------
# internal_config helpers
# ---------------------------------------------------------------------------

def _get_config_value(
    internal_config: dict | None,
    stage: str,
    key: str,
    default: Any = None,
) -> Any:
    """Read a value from internal_config[stage][key] with a fallback default."""
    if internal_config and stage in internal_config:
        stage_config = internal_config[stage]
        if key in stage_config:
            return stage_config[key]
    return default


# ---------------------------------------------------------------------------
# Step 1: Pose matching (keyword-based, adapted from gateway/routes/poses.py)
# ---------------------------------------------------------------------------

def _recommend_poses_sync(
    config: GatewayConfig, prompt: str, top_k: int = 5, min_score: float = 0.5
) -> list[dict]:
    """Keyword-based pose recommendation against MySQL poses table.

    Returns list of pose dicts (with id, pose_key, score), sorted by score desc.
    """
    prompt_lower = prompt.lower()
    conn = _get_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM poses WHERE enabled = 1")
            all_poses = cur.fetchall()
    finally:
        conn.close()

    scored: list[tuple[int, dict]] = []
    for pose in all_poses:
        score = 0
        pose_key = (pose.get("pose_key") or "").lower()
        name_en = (pose.get("name_en") or "").lower()
        name_cn = (pose.get("name_cn") or "").lower()

        if pose_key and pose_key in prompt_lower:
            score += 10
        if name_en:
            for word in name_en.split():
                if word in prompt_lower:
                    score += 3
        if name_cn and name_cn in prompt_lower:
            score += 5
        if pose_key:
            for token in pose_key.split("_"):
                if token in prompt_lower:
                    score += 2

        if score > 0:
            scored.append((score, pose))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {**p, "score": s}
        for s, p in scored[:top_k]
        if s >= min_score
    ]


# ---------------------------------------------------------------------------
# Step 2: Pose config + LoRA extraction from MySQL
# ---------------------------------------------------------------------------

def _fetch_pose_loras_and_refs(
    config: GatewayConfig, pose_id: int
) -> tuple[list[dict], list[dict], list[dict]]:
    """Fetch image_loras, video_loras, and reference_images for a pose.

    Queries pose_loras (split by lora_type) and pose_reference_images,
    then enriches LoRA data from lora_metadata.

    Returns: (image_loras, video_loras, reference_images)
    """
    conn = _get_connection(config)
    try:
        with conn.cursor() as cur:
            # Image loras
            cur.execute(
                "SELECT pl.* FROM pose_loras pl "
                "WHERE pl.pose_id = %s AND pl.lora_type = 'image' "
                "ORDER BY COALESCE(pl.sort_order, pl.id), "
                "pl.is_default DESC, pl.recommended_weight DESC",
                (pose_id,),
            )
            image_loras_raw = list(cur.fetchall())

            # Video loras
            cur.execute(
                "SELECT pl.* FROM pose_loras pl "
                "WHERE pl.pose_id = %s AND pl.lora_type = 'video' "
                "ORDER BY COALESCE(pl.sort_order, pl.id), "
                "pl.is_default DESC, pl.noise_stage",
                (pose_id,),
            )
            video_loras_raw = list(cur.fetchall())

            # Reference images
            cur.execute(
                "SELECT * FROM pose_reference_images WHERE pose_id = %s "
                "ORDER BY is_default DESC, quality_score DESC",
                (pose_id,),
            )
            reference_images = list(cur.fetchall())
    finally:
        conn.close()

    # Mark top-5 of each type as enabled
    for idx, lora in enumerate(image_loras_raw):
        lora["enabled"] = idx < 5
    for idx, lora in enumerate(video_loras_raw):
        lora["enabled"] = idx < 5

    # Enrich with lora_metadata (preview_url, trigger_words, etc.)
    all_lora_ids = [
        l["lora_id"]
        for l in image_loras_raw + video_loras_raw
        if l.get("lora_id")
    ]
    if all_lora_ids:
        meta_map = _fetch_lora_metadata_by_ids(config, all_lora_ids)
        for lora in image_loras_raw + video_loras_raw:
            lid = lora.get("lora_id")
            if lid and lid in meta_map:
                meta = meta_map[lid]
                lora["lora_name"] = meta.get("name") or meta.get("file") or lora.get("lora_name", "")
                lora["preview_url"] = meta.get("preview_url")
                lora["civitai_id"] = meta.get("civitai_id")
                tw = meta.get("trigger_words") or []
                if isinstance(tw, str):
                    try:
                        tw = json.loads(tw)
                    except Exception:
                        tw = []
                lora["trigger_words"] = tw
                lora["trigger_prompt"] = meta.get("trigger_prompt") or None

    return image_loras_raw, video_loras_raw, reference_images


def _fetch_lora_metadata_by_ids(
    config: GatewayConfig, lora_ids: list[int]
) -> dict[int, dict]:
    """Fetch lora_metadata rows for a list of IDs. Returns {id: row_dict}."""
    if not lora_ids:
        return {}
    conn = _get_connection(config)
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(lora_ids))
            cur.execute(
                f"SELECT id, name, file, preview_url, civitai_id, "
                f"trigger_words, trigger_prompt, example_prompts, description "
                f"FROM lora_metadata WHERE id IN ({placeholders})",
                lora_ids,
            )
            return {row["id"]: row for row in cur.fetchall()}
    finally:
        conn.close()


def _select_loras_from_pose(
    image_loras_raw: list[dict], video_loras_raw: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Deduplicate and normalize pose loras into image_loras and video_loras.

    Mirrors the old _select_loras_from_pose but works on raw DB rows
    rather than PoseConfig objects.
    """
    # Deduplicate image_loras by lora_id
    image_loras_dict: dict[Any, dict] = {}
    for lora in image_loras_raw:
        lid = lora.get("lora_id")
        if lid and lid not in image_loras_dict and lora.get("enabled", True):
            image_loras_dict[lid] = lora

    # Deduplicate video_loras by lora_name
    video_loras_dict: dict[Any, dict] = {}
    for lora in video_loras_raw:
        lid = lora.get("lora_id")
        lora_name = lora.get("lora_name", "")
        dedup_key = lora_name or lid
        if dedup_key and dedup_key not in video_loras_dict and lora.get("enabled", True):
            video_loras_dict[dedup_key] = lora

    def _normalize(lora_raw: dict) -> dict:
        return {
            "lora_id": lora_raw.get("lora_id"),
            "name": lora_raw.get("lora_name", ""),
            "weight": lora_raw.get("recommended_weight", 1.0),
            "trigger_words": lora_raw.get("trigger_words") or [],
            "trigger_prompt": lora_raw.get("trigger_prompt") or None,
            "noise_stage": lora_raw.get("noise_stage") or None,
        }

    image_loras = [_normalize(l) for l in image_loras_dict.values()]
    video_loras = [_normalize(l) for l in video_loras_dict.values()]
    return image_loras, video_loras


# ---------------------------------------------------------------------------
# Step 2b: Semantic search fallback via MySQL keyword matching
# ---------------------------------------------------------------------------

def _keyword_search_video_loras_sync(
    config: GatewayConfig, prompt: str, mode: str, top_k: int = 5
) -> list[dict]:
    """Fallback LoRA search using keyword matching on lora_metadata."""
    keywords = [w.strip().lower() for w in prompt.split() if len(w.strip()) >= 2]
    if not keywords:
        return []

    conditions: list[str] = []
    params: list[str] = []
    for kw in keywords[:10]:
        pattern = f"%{kw}%"
        conditions.append(
            "(LOWER(name) LIKE %s OR LOWER(description) LIKE %s "
            "OR LOWER(trigger_words) LIKE %s)"
        )
        params.extend([pattern, pattern, pattern])

    where_clause = " OR ".join(conditions)
    mode_filter = "T2V" if mode in (None, "t2v") else "I2V"

    query = (
        "SELECT id, name, mode, trigger_words, trigger_prompt, noise_stage "
        f"FROM lora_metadata WHERE (enabled = 1) AND mode = %s AND ({where_clause}) "
        f"ORDER BY id ASC LIMIT %s"
    )
    params_final: list[Any] = [mode_filter] + params + [top_k * 3]

    conn = _get_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(query, params_final)
            rows = cur.fetchall()
    finally:
        conn.close()

    # Score by keyword hits
    scored: list[tuple[int, dict]] = []
    for row in rows:
        searchable = " ".join(
            str(row.get(f, "") or "").lower()
            for f in ("name", "description", "trigger_words")
        )
        hits = sum(1 for kw in keywords if kw in searchable)
        scored.append((hits, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:top_k]]


# ---------------------------------------------------------------------------
# Step 3: Default LoRA injection (instagirl_v2 for T2V)
# ---------------------------------------------------------------------------

# Module-level cache for instagirl_v2 metadata
_instagirl_cache: dict | None = None


def _load_instagirl_metadata_sync(config: GatewayConfig) -> dict:
    """Load instagirl_v2 metadata from MySQL (cached after first call)."""
    global _instagirl_cache
    if _instagirl_cache is not None:
        return _instagirl_cache

    fallback = {
        "name": "instagirl_v2",
        "trigger_words": [],
        "trigger_prompt": None,
        "example_prompts": [],
        "description": "",
    }

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trigger_words, trigger_prompt, description, example_prompts "
                    "FROM lora_metadata WHERE name = %s",
                    ("instagirl_v2",),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            logger.warning("instagirl_v2 not found in lora_metadata")
            _instagirl_cache = fallback
            return _instagirl_cache

        trigger_words = _parse_json_field(row.get("trigger_words"))

        raw_ep = row.get("example_prompts")
        example_prompts = _parse_json_field(raw_ep)

        trigger_prompt = row.get("trigger_prompt") or None
        if trigger_prompt and trigger_prompt.strip() not in example_prompts:
            example_prompts = [trigger_prompt.strip()] + example_prompts

        _instagirl_cache = {
            "name": "instagirl_v2",
            "trigger_words": trigger_words,
            "trigger_prompt": trigger_prompt,
            "example_prompts": example_prompts,
            "description": row.get("description") or "",
        }
        logger.info(
            "Loaded instagirl_v2 metadata (trigger_words=%s, examples=%d)",
            trigger_words,
            len(example_prompts),
        )
        return _instagirl_cache

    except Exception as exc:
        logger.warning("Failed to load instagirl_v2 metadata: %s", exc)
        _instagirl_cache = fallback
        return _instagirl_cache


def _ensure_default_loras(
    video_loras: list[dict],
    mode: str,
    is_continuation: bool,
    config: GatewayConfig,
) -> list[dict]:
    """Ensure default LoRAs are present (immutable -- returns new list).

    Rules:
    - T2V only (NOT continuations): add instagirl_v2 to video_loras
      - weight 0.3 if other video LoRAs already exist
      - weight 0.6 if no other video LoRAs
    """
    if mode != "t2v" or is_continuation:
        return video_loras

    if any(l.get("name") == "instagirl_v2" for l in video_loras):
        return video_loras

    meta = _load_instagirl_metadata_sync(config)
    weight = 0.3 if video_loras else 0.6
    instagirl_lora = {
        "lora_id": None,
        "name": "instagirl_v2",
        "weight": weight,
        "trigger_words": meta["trigger_words"],
        "trigger_prompt": meta["trigger_prompt"],
        "noise_stage": None,
    }
    logger.info(
        "Default LoRA: adding instagirl_v2 to video_loras (weight=%s, has_other_loras=%s)",
        weight,
        bool(video_loras),
    )
    return video_loras + [instagirl_lora]


# ---------------------------------------------------------------------------
# Step 4: Video prompt generation
# ---------------------------------------------------------------------------

def _collect_trigger_words(loras: list[dict]) -> list[str]:
    """Flatten and deduplicate trigger_words from a list of LoRA dicts."""
    words: list[str] = []
    for lora in loras:
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            try:
                tw = json.loads(tw)
            except Exception:
                tw = []
        for w in (tw or []):
            if w and w not in words:
                words.append(w)
    return words


def _build_video_prompt(user_prompt: str, video_loras: list[dict]) -> str:
    """Build video prompt: user prompt + trigger words (no LLM call).

    LLM optimization is deferred to a later stage (generate_video_prompt)
    which runs after the first frame is ready.
    """
    trigger_words = _collect_trigger_words(video_loras)
    if trigger_words:
        return f"{user_prompt}, {', '.join(trigger_words)}"
    return user_prompt


# ---------------------------------------------------------------------------
# Step 5: T2I prompt generation (template-based)
# ---------------------------------------------------------------------------

def _build_t2i_prompt(
    user_prompt: str,
    image_loras: list[dict],
    optimized_base: str | None = None,
) -> tuple[str, str]:
    """Build T2I positive and negative prompts (template, no LLM).

    Positive = [quality tags] + [trigger_words/trigger_prompt] + [base] + [<lora:id:weight>]
    Negative = [quality negative tags]
    """
    base = optimized_base if optimized_base else user_prompt

    trigger_parts: list[str] = []
    lora_tags: list[str] = []
    for lora in image_loras[:3]:
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            try:
                tw = json.loads(tw)
            except Exception:
                tw = []
        for word in (tw or []):
            if word and word not in trigger_parts:
                trigger_parts.append(word)

        tp = lora.get("trigger_prompt")
        if tp and tp.strip() and tp.strip() not in trigger_parts:
            trigger_parts.append(tp.strip())

        lora_id = lora.get("lora_id", "")
        lora_weight = lora.get("weight", 0.8)
        if lora_id:
            lora_tags.append(f"<lora:{lora_id}:{lora_weight}>")

    parts = [T2I_POSITIVE_TAGS]
    if trigger_parts:
        parts.append(", ".join(trigger_parts))
    parts.append(base)
    positive = ", ".join(parts)
    if lora_tags:
        positive = positive + " " + " ".join(lora_tags)

    return positive, T2I_NEGATIVE_TAGS


# ---------------------------------------------------------------------------
# Metadata enrichment
# ---------------------------------------------------------------------------

def _enrich_loras_from_db(
    config: GatewayConfig,
    image_loras: list[dict],
    video_loras: list[dict],
) -> None:
    """Fetch preview_url and enrich trigger data from lora_metadata.

    Mutates the lora dicts in-place (acceptable because they are freshly
    constructed lists local to this call, not shared state).
    """
    all_lora_ids = [
        l["lora_id"]
        for l in image_loras + video_loras
        if l.get("lora_id")
    ]
    if not all_lora_ids:
        return

    try:
        meta_map = _fetch_lora_metadata_by_ids(config, all_lora_ids)
    except Exception as exc:
        logger.warning("Failed to fetch lora metadata: %s", exc)
        return

    for lora in image_loras + video_loras:
        meta = meta_map.get(lora.get("lora_id"))
        if not meta:
            continue
        lora["preview_url"] = meta.get("preview_url")

        if not lora.get("trigger_words"):
            lora["trigger_words"] = _parse_json_field(meta.get("trigger_words"))
        if not lora.get("trigger_prompt"):
            lora["trigger_prompt"] = meta.get("trigger_prompt") or None
        if not lora.get("example_prompts"):
            lora["example_prompts"] = _parse_json_field(meta.get("example_prompts"))


# ---------------------------------------------------------------------------
# Synchronous orchestration (runs in thread)
# ---------------------------------------------------------------------------

def _analyze_prompt_sync(
    user_prompt: str,
    mode: str,
    pose_keys: list[str] | None,
    internal_config: dict | None,
    config: GatewayConfig,
    is_continuation: bool,
) -> AnalysisResult:
    """Full Stage 1 pipeline (synchronous, runs in asyncio.to_thread).

    5 steps:
    1. Pose matching (auto-recommend if not provided)
    2. LoRA selection (from pose DB)
    3. Default LoRA injection (instagirl_v2 for T2V)
    4. Video prompt generation (trigger words appended)
    5. T2I prompt generation (quality tags + lora tags)
    """
    auto_completion = int(
        _get_config_value(internal_config, "stage1_prompt_analysis", "auto_completion", 2)
    )
    auto_prompt = _get_config_value(
        internal_config, "stage1_prompt_analysis", "auto_prompt", True
    )
    skip_llm = not auto_prompt
    if auto_completion < 1:
        skip_llm = True  # noqa: F841 — reserved for future LLM integration

    # ── Step 1: Pose matching ──────────────────────────────────────────
    effective_pose_keys: list[str] = list(pose_keys) if pose_keys else []
    if not effective_pose_keys and auto_completion >= 2:
        try:
            pose_min_score = 0.5 if mode in (None, "t2v", "first_frame") else 0.3
            recommendations = _recommend_poses_sync(
                config, user_prompt, top_k=5, min_score=pose_min_score
            )
            if recommendations:
                best = recommendations[0]
                effective_pose_keys = [best["pose_key"]]
                logger.info(
                    "Auto-selected pose: %s (score: %s)",
                    best["pose_key"],
                    best.get("score"),
                )
        except Exception as exc:
            logger.warning("Auto pose recommendation failed: %s", exc)
    elif not effective_pose_keys:
        logger.info("Pose matching skipped (auto_completion=%d < 2)", auto_completion)

    # ── Step 2: LoRA selection ─────────────────────────────────────────
    image_loras: list[dict] = []
    video_loras: list[dict] = []
    reference_image: str | None = None
    reference_skip_reactor: bool = False

    if effective_pose_keys:
        # Look up pose IDs from pose_key
        all_image_loras_raw: list[dict] = []
        all_video_loras_raw: list[dict] = []
        all_reference_images: list[dict] = []

        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                for pk in effective_pose_keys:
                    cur.execute(
                        "SELECT id FROM poses WHERE pose_key = %s AND enabled = 1",
                        (pk,),
                    )
                    row = cur.fetchone()
                    if not row:
                        continue
                    pose_id = row["id"]

                    il, vl, refs = _fetch_pose_loras_and_refs(config, pose_id)
                    all_image_loras_raw.extend(il)
                    all_video_loras_raw.extend(vl)
                    all_reference_images.extend(refs)
        finally:
            conn.close()

        if all_image_loras_raw or all_video_loras_raw:
            image_loras, video_loras = _select_loras_from_pose(
                all_image_loras_raw, all_video_loras_raw
            )

        # Pick a random reference image
        if all_reference_images:
            selected_ref = random.choice(all_reference_images)
            reference_image = selected_ref.get("image_url")
            reference_skip_reactor = bool(selected_ref.get("skip_reactor", 0))

        logger.info(
            "Pose LoRA selection: %d image, %d video, ref_image=%s",
            len(image_loras),
            len(video_loras),
            "yes" if reference_image else "no",
        )

    # ── Step 2b: Keyword search fallback when pose yields no video LoRAs
    if not video_loras and auto_completion >= 2:
        try:
            results = _keyword_search_video_loras_sync(
                config, user_prompt, mode, top_k=5
            )
            if results:
                best = results[0]
                tw = best.get("trigger_words")
                if isinstance(tw, str):
                    try:
                        tw = json.loads(tw)
                    except Exception:
                        tw = []
                video_loras.append({
                    "lora_id": best["id"],
                    "name": best.get("name", ""),
                    "weight": 0.8,
                    "trigger_words": tw or [],
                    "trigger_prompt": best.get("trigger_prompt") or None,
                    "noise_stage": best.get("noise_stage"),
                })
                logger.info(
                    "Keyword LoRA fallback: selected '%s' (id=%s)",
                    best.get("name"),
                    best["id"],
                )
            else:
                logger.info("Keyword LoRA fallback: no match found")
        except Exception as exc:
            logger.warning("Keyword LoRA fallback failed: %s", exc)

    # Enrich all loras with preview_url / trigger data
    _enrich_loras_from_db(config, image_loras, video_loras)

    # ── Step 3: Default LoRA injection ─────────────────────────────────
    if auto_completion >= 2:
        video_loras = _ensure_default_loras(
            video_loras, mode, is_continuation=is_continuation, config=config
        )
    else:
        logger.info(
            "Default LoRA injection skipped (auto_completion=%d < 2)",
            auto_completion,
        )

    # ── Step 4: Video prompt ───────────────────────────────────────────
    video_prompt = _build_video_prompt(user_prompt, video_loras)

    # ── Step 5: T2I prompt ─────────────────────────────────────────────
    t2i_prompt, t2i_negative_prompt = _build_t2i_prompt(user_prompt, image_loras)

    return AnalysisResult(
        video_prompt=video_prompt,
        t2i_prompt=t2i_prompt,
        t2i_negative_prompt=t2i_negative_prompt,
        image_loras=image_loras,
        video_loras=video_loras,
        reference_image=reference_image,
        reference_skip_reactor=reference_skip_reactor,
        pose_keys=effective_pose_keys,
        original_prompt=user_prompt,
        optimized_prompt=video_prompt,
    )


# ---------------------------------------------------------------------------
# Public async entry point
# ---------------------------------------------------------------------------

async def analyze_prompt(
    user_prompt: str,
    mode: str,
    pose_keys: list[str] | None,
    internal_config: dict | None,
    config: GatewayConfig,
    redis: Any = None,
    is_continuation: bool = False,
) -> AnalysisResult:
    """Full Stage 1 pipeline (async wrapper).

    Args:
        user_prompt: Raw user prompt text.
        mode: Generation mode ("t2v", "first_frame", "face_reference",
              "full_body_reference").
        pose_keys: Optional list of pose keys to use (skips auto-recommend).
        internal_config: Optional per-stage configuration overrides.
        config: GatewayConfig with MySQL and other settings.
        redis: Redis connection (used by the 3-stage pose recommender).
        is_continuation: True if this is a cross-workflow continuation.

    Returns:
        AnalysisResult with all prompts, loras, and references.
    """
    # ── Stage 1 (new): pose recommendation via gpu/inference_worker ───
    # If the caller didn't supply pose_keys and auto_completion >= 2, run
    # the 3-stage recommender (synonym + embedding + LLM rerank) to pick the
    # best pose. The result is fed into _analyze_prompt_sync as if the user
    # had supplied it. This replaces the broken naive substring matcher
    # in _recommend_poses_sync.
    auto_completion = int(
        _get_config_value(internal_config, "stage1_prompt_analysis", "auto_completion", 2)
    )
    effective_pose_keys = list(pose_keys) if pose_keys else []
    if not effective_pose_keys and auto_completion >= 2 and redis is not None:
        try:
            from api_gateway.services.pose_recommender import get_pose_recommender

            pose_min_score = 0.5 if mode in (None, "t2v", "first_frame") else 0.3
            recommender = get_pose_recommender(config, redis)
            recs = await recommender.recommend(
                user_prompt, top_k=5, min_score=pose_min_score,
            )
            if recs:
                effective_pose_keys = [recs[0].pose_key]
                logger.info(
                    "PoseRecommender picked %s (score=%.3f, reason=%s)",
                    recs[0].pose_key, recs[0].score, recs[0].match_reason,
                )
        except Exception as exc:
            logger.warning(
                "PoseRecommender failed (%s); _analyze_prompt_sync will fall "
                "back to its legacy substring matcher",
                exc,
            )

    try:
        return await asyncio.to_thread(
            _analyze_prompt_sync,
            user_prompt,
            mode,
            effective_pose_keys,
            internal_config,
            config,
            is_continuation,
        )
    except Exception as exc:
        logger.error("Prompt analysis failed: %s", exc, exc_info=True)
        # Return a minimal result with just the original prompt
        return AnalysisResult(
            video_prompt=user_prompt,
            t2i_prompt=user_prompt,
            t2i_negative_prompt=T2I_NEGATIVE_TAGS,
            original_prompt=user_prompt,
            optimized_prompt=user_prompt,
        )
