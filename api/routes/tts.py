"""TTS (Text-to-Speech) API using ChatTTS."""

import logging
import uuid
import numpy as np
import scipy.io.wavfile as wavfile
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from api.config import UPLOADS_DIR

logger = logging.getLogger(__name__)
router = APIRouter()

# Lazy-loaded ChatTTS instance
_chat = None
_chat_loading = False


def _get_chat():
    """Lazy-load ChatTTS model (first call takes ~5s)."""
    global _chat, _chat_loading
    if _chat is not None:
        return _chat
    if _chat_loading:
        raise HTTPException(503, "ChatTTS model is loading, please retry in a few seconds")
    _chat_loading = True
    try:
        import ChatTTS
        chat = ChatTTS.Chat()
        chat.load(compile=False)
        _chat = chat
        logger.info("ChatTTS model loaded successfully")
        return _chat
    except Exception as e:
        _chat_loading = False
        logger.error("Failed to load ChatTTS: %s", e)
        raise HTTPException(500, f"Failed to load ChatTTS: {e}")


class TTSRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize")
    seed: Optional[int] = Field(None, description="Random seed for voice consistency (same seed = same voice)")
    temperature: float = Field(0.3, ge=0.01, le=1.0, description="Temperature for generation (lower = more stable)")
    top_p: float = Field(0.7, ge=0.1, le=1.0, description="Top-p sampling")
    top_k: int = Field(20, ge=1, le=100, description="Top-k sampling")
    speed: int = Field(5, ge=0, le=9, description="Speech speed: 0=slowest, 5=normal, 9=fastest")
    oral: int = Field(0, ge=0, le=9, description="Oral/casual level: 0=formal, 9=very casual")
    laugh: int = Field(0, ge=0, le=2, description="Laugh level: 0=none, 1=light, 2=more")
    pause: int = Field(3, ge=0, le=7, description="Pause frequency: 0=minimal, 7=many pauses")


class TTSResponse(BaseModel):
    audio_file: str
    duration: float
    sample_rate: int
    filename: str


@router.post("/tts", response_model=TTSResponse)
async def text_to_speech(req: TTSRequest):
    """Generate speech audio from text using ChatTTS."""
    import torch

    chat = _get_chat()

    # Build refine text prompt (controls auto-inserted emotions/pauses)
    refine_prompt = f"[oral_{req.oral}][laugh_{req.laugh}][break_{req.pause}]"

    params_refine = chat.RefineTextParams(
        prompt=refine_prompt,
    )

    # Build infer prompt with speed control
    infer_prompt = f"[speed_{req.speed}]"

    # Generate speaker embedding from seed for consistent voice
    params_infer = chat.InferCodeParams(
        prompt=infer_prompt,
        temperature=req.temperature,
        top_P=req.top_p,
        top_K=req.top_k,
    )

    if req.seed is not None:
        torch.manual_seed(req.seed)
        params_infer.spk_emb = chat.sample_random_speaker()

    try:
        wavs = chat.infer(
            [req.text],
            params_refine_text=params_refine,
            params_infer_code=params_infer,
        )
    except Exception as e:
        logger.error("ChatTTS inference failed: %s", e)
        raise HTTPException(500, f"TTS generation failed: {e}")

    audio = wavs[0]
    if audio is None or len(audio) == 0:
        raise HTTPException(500, "TTS generated empty audio")

    # Save as WAV
    sample_rate = 24000
    duration = len(audio) / sample_rate
    filename = f"tts_{uuid.uuid4().hex[:8]}.wav"
    output_path = UPLOADS_DIR / filename

    audio_int16 = (audio * 32767).astype(np.int16)
    wavfile.write(str(output_path), sample_rate, audio_int16)

    logger.info("TTS generated: %s (%.1fs, %d samples)", filename, duration, len(audio))

    return TTSResponse(
        audio_file=f"/api/v1/results/{filename}",
        duration=round(duration, 2),
        sample_rate=sample_rate,
        filename=filename,
    )


@router.get("/tts/voices")
async def list_voices():
    """List available voice seeds for preview."""
    return {
        "info": "Use 'seed' parameter for consistent voice. Same seed = same voice.",
        "suggested_seeds": [
            {"seed": 42, "description": "Female, gentle"},
            {"seed": 123, "description": "Male, deep"},
            {"seed": 456, "description": "Female, energetic"},
            {"seed": 789, "description": "Male, calm"},
            {"seed": 2024, "description": "Female, soft"},
        ],
        "note": "Voice characteristics vary - try different seeds to find one you like",
    }
