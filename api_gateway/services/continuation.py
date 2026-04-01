"""VLM + LLM helpers for auto-continue chain orchestration.

Calls a vision model to describe the last frame of a segment, then an LLM
to generate a continuation prompt for the next segment.

Both calls are optional: if the API key is not configured the functions
return None and the caller falls back to the pre-set segment prompt.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# System prompt instructing the VLM to produce a concise frame description
_VLM_SYSTEM = (
    "You are a visual analysis assistant. "
    "Describe the image in 1-2 sentences focusing on: "
    "subject position, lighting, environment, and any motion cues. "
    "Be concise and factual."
)

# System prompt instructing the LLM to continue the story
_LLM_SYSTEM = (
    "You are a creative video director. "
    "Given a description of the last frame of a video segment, "
    "write a SHORT continuation prompt (≤30 words) for the NEXT segment. "
    "The continuation should feel like a natural, cinematic follow-on. "
    "Output only the prompt text, no explanations."
)


async def describe_frame(
    image_url: str,
    api_key: str,
    base_url: str,
    model: str,
) -> Optional[str]:
    """Call a vision-capable LLM to describe *image_url*.

    Returns the description string, or None on failure.
    """
    if not api_key or not image_url:
        return None

    try:
        from openai import AsyncOpenAI  # type: ignore

        client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _VLM_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": "low"},
                        },
                        {"type": "text", "text": "Describe this frame."},
                    ],
                },
            ],
            max_tokens=200,
        )
        description = response.choices[0].message.content or ""
        logger.debug("Frame description: %s", description)
        return description.strip()
    except Exception as exc:
        logger.warning("VLM call failed: %s", exc)
        return None


async def generate_continuation_prompt(
    frame_description: str,
    previous_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
) -> Optional[str]:
    """Call an LLM to generate a continuation prompt.

    Returns the next-segment prompt string, or None on failure.
    """
    if not api_key or not frame_description:
        return None

    try:
        from openai import AsyncOpenAI  # type: ignore

        client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        user_msg = (
            f"Previous segment prompt: {previous_prompt}\n"
            f"Last frame description: {frame_description}\n"
            "Write the continuation prompt for the next segment."
        )
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=100,
        )
        prompt = response.choices[0].message.content or ""
        logger.debug("Continuation prompt: %s", prompt)
        return prompt.strip()
    except Exception as exc:
        logger.warning("LLM continuation call failed: %s", exc)
        return None
