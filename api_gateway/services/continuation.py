"""VLM + LLM helpers for auto-continue chain orchestration.

Used by ``chain_orchestrator.py`` to compute the prompt for segment N+1
based on the last frame of segment N. All GPU work goes through
``InferenceClient`` (Redis → ``gpu/inference_worker`` → loopback vLLM on
148) so the gateway never needs direct network reachability to the vLLM
endpoints.

Both functions are graceful: on any failure they return ``None`` and the
caller is expected to fall back to whatever pre-set prompt the user
supplied for that segment.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

import aiohttp

from api_gateway.services.gpu_clients.inference import (
    InferenceClient,
    InferenceError,
    InferenceTimeout,
)

logger = logging.getLogger(__name__)


# System / instruction prompts that used to be wired into the openai SDK
# directly. Kept here so the helpers stay self-contained.
_VLM_INSTRUCTION = (
    "Describe this image in 1-2 sentences focusing on: subject position, "
    "lighting, environment, and any motion cues. Be concise and factual."
)

_LLM_SYSTEM = (
    "/no_think You are a creative video director. Given a description of "
    "the last frame of a video segment, write a SHORT continuation prompt "
    "(≤30 words) for the NEXT segment. The continuation should feel like "
    "a natural, cinematic follow-on. Output only the prompt text, no "
    "explanations, no thinking tags."
)


async def _download_to_b64(image_url: str, *, timeout: float = 15.0) -> Optional[str]:
    """Fetch an image URL and return its raw base64 (no data: prefix)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                image_url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "describe_frame: GET %s returned HTTP %d",
                        image_url[:80], resp.status,
                    )
                    return None
                data = await resp.read()
                return base64.b64encode(data).decode("ascii")
    except Exception as exc:
        logger.warning("describe_frame: failed to download %s: %s", image_url[:80], exc)
        return None


async def describe_frame(
    image_url: str,
    *,
    redis,
    model: str = "",
    # Legacy positional args kept for source-compatibility with the old
    # signature (api_key/base_url were openai SDK params). Ignored.
    api_key: str = "",
    base_url: str = "",
) -> Optional[str]:
    """Use VLM (Qwen2.5-VL via inference_worker) to describe ``image_url``.

    Returns the description string, or ``None`` on failure (image fetch
    failed, worker unreachable, etc).
    """
    if not image_url:
        return None
    if redis is None:
        logger.warning("describe_frame: no redis connection, cannot call inference_worker")
        return None

    raw_b64 = await _download_to_b64(image_url)
    if not raw_b64:
        return None

    try:
        client = InferenceClient(redis)
        text = await client.describe_image(
            image_b64=raw_b64,
            prompt=_VLM_INSTRUCTION,
            model=model or None,
            max_tokens=200,
            temperature=0.3,
            timeout=60.0,
        )
        text = (text or "").strip()
        logger.debug("Frame description: %s", text)
        return text or None
    except (InferenceError, InferenceTimeout) as exc:
        logger.warning("describe_frame: VLM call failed: %s", exc)
        return None
    except Exception as exc:
        logger.exception("describe_frame: unexpected error: %s", exc)
        return None


async def generate_continuation_prompt(
    frame_description: str,
    previous_prompt: str,
    *,
    redis,
    model: str = "",
    # Legacy kwargs kept for compatibility with the old signature
    api_key: str = "",
    base_url: str = "",
) -> Optional[str]:
    """Ask the LLM (Qwen3-14B via inference_worker) for the next-segment prompt.

    This is the **simple** continuation generator: VLM frame description +
    previous prompt → 1-2 sentence cinematic follow-on. Used as a fallback
    when ``rich_continuation_prompt`` (story-arc aware) fails.

    Returns the continuation prompt string, or ``None`` on failure.
    """
    if not frame_description:
        return None
    if redis is None:
        logger.warning("generate_continuation_prompt: no redis connection")
        return None

    user_msg = (
        f"Previous segment prompt: {previous_prompt}\n"
        f"Last frame description: {frame_description}\n"
        "Write the continuation prompt for the next segment."
    )

    try:
        client = InferenceClient(redis)
        text = await client.chat(
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            model=model or None,
            max_tokens=100,
            temperature=0.7,
            timeout=30.0,
        )
        text = (text or "").strip()
        logger.debug("Continuation prompt: %s", text)
        return text or None
    except (InferenceError, InferenceTimeout) as exc:
        logger.warning("generate_continuation_prompt: LLM call failed: %s", exc)
        return None
    except Exception as exc:
        logger.exception("generate_continuation_prompt: unexpected error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Rich continuation prompt (Fix 2)
# ---------------------------------------------------------------------------
#
# This wraps the OLD monolith's PromptOptimizer.generate_continuation_prompt,
# which has features the simple version above lacks:
#
#   1. ``match_story_arcs(text)`` — looks up the matching narrative arc
#      (e.g. "sex_oral", "sex_intercourse", "undressing") from
#      config/story_arcs.yaml and tells the LLM where in the story arc the
#      content currently is, so the next segment progresses naturally.
#   2. ``WORKFLOW_CONTINUE_PROMPT_TEMPLATE`` — a much richer system prompt
#      that distinguishes "explicit user intent" from "free narrative
#      inference", with rules about pacing, framing, action verbs, etc.
#   3. ``continuation_index`` awareness — the LLM is told whether this is
#      "DEVELOPMENT phase", "CLIMAX phase", or "RESOLUTION phase".
#
# The OLD method outputs ``(at 0 seconds: ...) (at 3 seconds: ...)``
# keyframe-format prompts because it was designed for a different downstream
# (the prompt_splitter that breaks long timelines into per-segment text).
# In the gateway's chain_orchestrator each segment is an independent video,
# so we POST-PROCESS the rich output via ``split_prompt_by_segments`` to
# strip the timestamps and produce one clean plain-text prompt.
#
# When anything fails (download, LLM, parsing) we return None and the caller
# falls back to the simple ``describe_frame`` + ``generate_continuation_prompt``
# pair above.


async def rich_continuation_prompt(
    *,
    frame_url: str,
    previous_prompt: str,
    user_intent: str = "",
    duration: float = 3.0,
    continuation_index: int = 1,
    redis,
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
    vision_api_key: str = "",
    vision_base_url: str = "",
    vision_model: str = "",
) -> Optional[str]:
    """Story-arc aware continuation prompt generator.

    Calls the OLD monolith's ``PromptOptimizer.generate_continuation_prompt``
    via the gateway's monkey-patched ``make_prompt_optimizer`` (so all LLM /
    VLM traffic goes through Redis → gpu/inference_worker, never direct HTTP).

    Returns a single plain-text prompt string suitable for handing directly
    to the next chain segment, or ``None`` on any failure.
    """
    if not frame_url:
        logger.info("rich_continuation_prompt: no frame_url provided")
        return None
    if redis is None:
        logger.warning("rich_continuation_prompt: no redis connection")
        return None

    raw_b64 = await _download_to_b64(frame_url)
    if not raw_b64:
        return None

    try:
        # Build the patched PromptOptimizer (monkey-patches _llm_call and
        # _describe_image to go through InferenceClient).
        from api_gateway.services.prompt_optimizer import make_prompt_optimizer

        optimizer = make_prompt_optimizer(
            llm_api_key=llm_api_key or "dummy",
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            vision_api_key=vision_api_key or "dummy",
            vision_base_url=vision_base_url,
            vision_model=vision_model,
            redis=redis,
        )

        # The rich method takes user_intent + previous_video_prompt + b64 frame.
        # When user_intent is empty/equal to previous, the rich version's
        # template kicks into "free inference" mode and uses narrative pacing.
        raw_text = await optimizer.generate_continuation_prompt(
            user_intent=user_intent or previous_prompt,
            previous_video_prompt=previous_prompt,
            frame_image_base64=raw_b64,
            duration=duration,
            continuation_index=continuation_index,
        )
    except Exception as exc:
        logger.warning("rich_continuation_prompt: optimizer call failed: %s", exc)
        return None

    raw_text = (raw_text or "").strip()
    if not raw_text:
        logger.info("rich_continuation_prompt: optimizer returned empty")
        return None

    # The rich generator outputs (at N seconds: ...) keyframe format intended
    # for the OLD monolith's prompt_splitter (which then feeds plain text to
    # individual segments). In the gateway's per-segment chain, each segment
    # is an independent video, so we use prompt_splitter ourselves with
    # total_duration == segment_duration to extract a single plain-text
    # prompt without the (at N seconds: ...) timestamps.
    plain = _strip_keyframes(raw_text, segment_duration=duration)
    if not plain:
        return raw_text  # last resort: return whatever the LLM produced
    logger.info(
        "rich_continuation_prompt: generated (continuation_index=%d, %d chars): %s",
        continuation_index, len(plain), plain[:200],
    )
    return plain


def _strip_keyframes(text: str, *, segment_duration: float) -> str:
    """Use prompt_splitter to extract a plain-text prompt from a (at N s: ...) timeline.

    The OLD ``split_prompt_by_segments`` returns a list of per-segment plain-text
    strings; for our use case (single segment) we just take the first one. This
    correctly handles all the edge cases the splitter knows about (multiple
    keyframes within a window, global context outside any (at ...) block, etc).
    """
    try:
        # The splitter lives in the OLD monolith but it's pure-python and has
        # no api.config dependency, so we can import it directly once
        # make_prompt_optimizer has put the project root on sys.path.
        from api.services.prompt_splitter import split_prompt_by_segments

        segments = split_prompt_by_segments(
            text,
            total_duration=max(segment_duration, 0.1),
            segment_duration=max(segment_duration, 0.1),
        )
        if segments:
            return segments[0].strip()
    except Exception as exc:
        logger.warning("_strip_keyframes: splitter failed (%s); using regex fallback", exc)

    # Fallback: simple regex strip of (at N seconds: ...) wrappers
    import re
    # Pull the inner content out of each (at N s: content) block
    parts = re.findall(r"\(at\s+[\d.]+\s*s(?:econds?)?\s*:\s*(.*?)\)", text, re.IGNORECASE | re.DOTALL)
    if parts:
        return ", ".join(p.strip() for p in parts).strip()
    # No keyframe markers at all — return the original text as-is
    return text.strip()
