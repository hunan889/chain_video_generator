import re
import math


def split_prompt_by_segments(prompt: str, total_duration: float, segment_duration: float) -> list[str]:
    """Split a prompt with (at N seconds: ...) timestamps into per-segment prompts.

    Strategy:
    - Extract global context (text outside timestamps) as a prefix for all segments
    - For each segment, combine: global context + previous keyframe (continuity) + current keyframe
    - This prevents sparse prompts and abrupt camera/subject transitions between segments
    """
    num_segments = max(1, math.ceil(total_duration / segment_duration))

    # Parse (at N seconds: description) or (at Ns: description)
    pattern = r'\(at\s+([\d.]+)\s*s(?:econds?)?\s*:\s*(.*?)\)'
    matches = re.findall(pattern, prompt, re.IGNORECASE | re.DOTALL)

    if not matches:
        return [prompt] * num_segments

    # Extract global context: text outside all (at ...) blocks, stripped
    global_ctx = re.sub(pattern, '', prompt, flags=re.IGNORECASE | re.DOTALL).strip()
    # Clean up extra whitespace/commas left behind
    global_ctx = re.sub(r'\s{2,}', ' ', global_ctx).strip(' ,')

    # Build timeline: [(time_sec, text), ...]
    timeline = sorted([(float(t), text.strip()) for t, text in matches], key=lambda x: x[0])

    segments = []
    for i in range(num_segments):
        seg_start = i * segment_duration
        seg_end = seg_start + segment_duration

        # Find the latest keyframe strictly before seg_start (continuity from previous)
        latest_before = None
        # Find keyframes that fall within this segment's time window [seg_start, seg_end)
        within = []
        for t, text in timeline:
            if t < seg_start:
                latest_before = text
            elif t < seg_end:
                within.append(text)

        # Build the segment prompt with layered context
        parts = []

        # 1. Global context prefix (subjects, scene, style — always present)
        if global_ctx:
            parts.append(global_ctx)

        # 2. Previous keyframe for continuity (only if current segment has its own keyframe)
        #    If no new keyframe in this segment, the previous one IS the main content
        if within:
            # Has new keyframes — add previous as continuity bridge
            if latest_before and i > 0:
                parts.append(latest_before)
            parts.extend(within)
        elif latest_before:
            # No new keyframe — continue with the latest previous one
            parts.append(latest_before)
        else:
            # Nothing applicable — use first keyframe as fallback
            parts.append(timeline[0][1])

        segments.append(", ".join(parts))

    return segments
