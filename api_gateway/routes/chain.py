"""Chain generation endpoints for the API gateway."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api_gateway.dependencies import get_chain_orchestrator, get_gateway
from api_gateway.services.chain_orchestrator import ChainOrchestrator
from shared.enums import ModelType
from shared.task_gateway import TaskGateway

router = APIRouter(prefix="/api/v1", tags=["chain"])


# -- Request / Response models ---------------------------------------------


class ChainSegment(BaseModel):
    prompt: str
    duration: float = 5.0
    workflow: Optional[dict] = None
    image_filename: Optional[str] = None
    extract_last_frame: bool = False


class ChainRequest(BaseModel):
    segments: list[ChainSegment] = Field(min_length=1)
    model: ModelType = ModelType.A14B
    auto_continue: bool = False


class ChainResponse(BaseModel):
    chain_id: str
    total_segments: int
    status: str


# -- Endpoints -------------------------------------------------------------


@router.post("/chains", response_model=ChainResponse)
async def create_chain(
    request: ChainRequest,
    gateway: TaskGateway = Depends(get_gateway),
    orchestrator: ChainOrchestrator = Depends(get_chain_orchestrator),
) -> ChainResponse:
    """Create and start a multi-segment chain generation."""

    params = {
        "model": request.model.value,
        "auto_continue": request.auto_continue,
        "segment_count": len(request.segments),
    }
    chain_id = await gateway.create_chain(len(request.segments), params)

    segments = [s.model_dump() for s in request.segments]
    await orchestrator.start_chain(
        chain_id=chain_id,
        segments=segments,
        model=request.model,
        auto_continue=request.auto_continue,
    )

    return ChainResponse(
        chain_id=chain_id,
        total_segments=len(request.segments),
        status="queued",
    )


@router.get("/chains/{chain_id}")
async def get_chain(
    chain_id: str,
    gateway: TaskGateway = Depends(get_gateway),
) -> dict:
    """Get chain status and progress."""
    chain = await gateway.get_chain(chain_id)
    if not chain:
        raise HTTPException(404, "Chain not found")
    return chain


@router.get("/chains")
async def list_chains(
    gateway: TaskGateway = Depends(get_gateway),
) -> list[dict]:
    """List all chains."""
    return await gateway.list_chains()


@router.post("/chains/{chain_id}/cancel")
async def cancel_chain(
    chain_id: str,
    gateway: TaskGateway = Depends(get_gateway),
    orchestrator: ChainOrchestrator = Depends(get_chain_orchestrator),
) -> dict:
    """Cancel a running chain."""
    result = await orchestrator.cancel_chain(chain_id)
    if not result:
        raise HTTPException(409, "Chain cannot be cancelled")
    return {"status": "cancelled"}
