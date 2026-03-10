"""BytePlus ModelArk chat/responses API proxy with streaming SSE."""

import json
import logging
import os

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional

from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

BYTEPLUS_API_KEY = os.getenv(
    "BYTEPLUS_API_KEY", "f3cb7588-0af7-4753-96c4-8ca992600bca"
)
BYTEPLUS_RESPONSES_URL = os.getenv(
    "BYTEPLUS_RESPONSES_URL",
    "https://ark.ap-southeast.bytepluses.com/api/v3/responses",
)
CHAT_MODEL = os.getenv("CHAT_MODEL", "ep-20260308123632-k8f6w")


class ChatMessage(BaseModel):
    role: str = "user"
    content: str


class McpTool(BaseModel):
    type: str = "mcp"
    server_label: str
    server_url: str
    require_approval: str = "never"


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., min_length=1)
    model: Optional[str] = Field(None, description="Override model/endpoint ID")
    stream: bool = True
    tools: Optional[List[McpTool]] = None


@router.post("/chat/completions")
async def chat_completions(req: ChatRequest, _user=Depends(verify_api_key)):
    """Proxy chat to BytePlus ModelArk responses API with SSE streaming."""
    model = req.model or CHAT_MODEL

    # Build the payload in BytePlus responses format
    payload = {
        "model": model,
        "stream": req.stream,
        "input": [
            {
                "role": msg.role,
                "content": [{"type": "input_text", "text": msg.content}],
            }
            for msg in req.messages
        ],
    }

    if req.tools:
        payload["tools"] = [t.model_dump() for t in req.tools]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BYTEPLUS_API_KEY}",
        "ark-beta-mcp": "true",
    }

    if not req.stream:
        # Non-streaming: forward response directly
        async with aiohttp.ClientSession() as session:
            async with session.post(
                BYTEPLUS_RESPONSES_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=300)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("BytePlus chat error %d: %s", resp.status, body[:500])
                    raise HTTPException(resp.status, f"BytePlus API error: {body[:500]}")
                data = await resp.json()
                return data

    # Streaming: proxy SSE
    async def stream_generator():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    BYTEPLUS_RESPONSES_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=300)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("BytePlus chat stream error %d: %s", resp.status, body[:500])
                        error = json.dumps({"error": body[:500]})
                        yield f"data: {error}\n\n"
                        return

                    async for line in resp.content:
                        decoded = line.decode("utf-8", errors="replace")
                        yield decoded
        except Exception as e:
            logger.error("Chat stream error: %s", e)
            error = json.dumps({"error": str(e)})
            yield f"data: {error}\n\n"

    logger.info("Chat stream: model=%s messages=%d", model, len(req.messages))
    return StreamingResponse(stream_generator(), media_type="text/event-stream")
