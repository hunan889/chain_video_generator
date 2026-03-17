"""
姿势匹配API路由
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from api.services.pose_matcher import get_pose_matcher, PoseMatch, PoseConfig

router = APIRouter()


class PoseMatchRequest(BaseModel):
    """姿势匹配请求"""
    query: str = Field(..., description="用户输入的查询文本")
    top_k: int = Field(5, description="返回top K个结果", ge=1, le=20)
    min_score: float = Field(0.3, description="最低匹配分数", ge=0.0, le=1.0)
    preferences: Optional[Dict] = Field(None, description="偏好设置")


class PoseMatchResponse(BaseModel):
    """姿势匹配响应"""
    matches: List[Dict]


class PoseConfigResponse(BaseModel):
    """姿势配置响应"""
    pose: Dict
    reference_images: List[Dict]
    image_loras: List[Dict]
    video_loras: List[Dict]
    prompt_templates: List[Dict]


class PoseListResponse(BaseModel):
    """姿势列表响应"""
    poses: List[Dict]


@router.post("/poses/match", response_model=PoseMatchResponse)
async def match_poses(request: PoseMatchRequest):
    """
    姿势匹配接口

    根据用户输入的自然语言查询，返回匹配的姿势列表

    示例:
    ```
    POST /api/v1/poses/match
    {
        "query": "a girl sex with a man like cow girl",
        "top_k": 3,
        "preferences": {
            "angle": "pov",
            "style": "realistic"
        }
    }
    ```
    """
    matcher = get_pose_matcher()
    matches = matcher.match_poses(
        query=request.query,
        top_k=request.top_k,
        min_score=request.min_score
    )

    # 转换为字典
    result_matches = []
    for match in matches:
        match_dict = {
            "pose_id": match.pose_id,
            "pose_key": match.pose_key,
            "name_en": match.name_en,
            "name_cn": match.name_cn,
            "description": match.description,
            "difficulty": match.difficulty,
            "category": match.category,
            "match_score": match.match_score,
            "matched_keywords": match.matched_keywords,
            "confidence": match.confidence
        }

        # 如果提供了preferences，获取完整配置
        if request.preferences:
            config = matcher.get_pose_config(match.pose_id, request.preferences)
            if config:
                match_dict["reference_images"] = config.reference_images
                match_dict["image_loras"] = config.image_loras
                match_dict["video_loras"] = config.video_loras
                match_dict["prompt_templates"] = config.prompt_templates

        result_matches.append(match_dict)

    return PoseMatchResponse(matches=result_matches)


@router.get("/poses/{pose_id}/config", response_model=PoseConfigResponse)
async def get_pose_config(
    pose_id: int,
    angle: Optional[str] = None,
    style: Optional[str] = None,
    noise_stage: Optional[str] = "high"
):
    """
    获取姿势的完整配置

    Args:
        pose_id: 姿势ID
        angle: 优先角度 (pov/front/back/side/top/close)
        style: 风格偏好 (realistic/anime)
        noise_stage: 噪声阶段 (high/low)

    示例:
    ```
    GET /api/v1/poses/3/config?angle=pov&style=realistic&noise_stage=high
    ```
    """
    matcher = get_pose_matcher()

    preferences = {}
    if angle:
        preferences["angle"] = angle
    if style:
        preferences["style"] = style
    if noise_stage:
        preferences["noise_stage"] = noise_stage

    config = matcher.get_pose_config(pose_id, preferences)

    if not config:
        raise HTTPException(status_code=404, detail=f"Pose {pose_id} not found")

    return PoseConfigResponse(
        pose=config.pose,
        reference_images=config.reference_images,
        image_loras=config.image_loras,
        video_loras=config.video_loras,
        prompt_templates=config.prompt_templates
    )


@router.get("/poses", response_model=PoseListResponse)
async def list_poses(category: Optional[str] = None):
    """
    列出所有姿势

    Args:
        category: 分类筛选 (position/oral/manual/other)

    示例:
    ```
    GET /api/v1/poses?category=position
    ```
    """
    matcher = get_pose_matcher()
    poses = matcher.list_all_poses(category=category)

    return PoseListResponse(poses=poses)
