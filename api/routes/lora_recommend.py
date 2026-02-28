from fastapi import APIRouter, Depends
from api.models.schemas import LoraRecommendRequest, LoraRecommendResponse
from api.middleware.auth import verify_api_key
from api.services.lora_selector import LoraSelector

router = APIRouter()
_lora_selector = LoraSelector()


@router.post("/loras/recommend", response_model=LoraRecommendResponse)
async def recommend_loras(req: LoraRecommendRequest, _=Depends(verify_api_key)):
    loras = await _lora_selector.select(req.prompt)
    return LoraRecommendResponse(loras=loras)
