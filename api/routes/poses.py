"""
姿势API路由
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict
from api.services.pose_matcher import get_pose_matcher, PoseConfig
from api.middleware.auth import verify_api_key
import sqlite3
from pathlib import Path

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
POSE_DB_PATH = PROJECT_ROOT / "data" / "wan22.db"


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


@router.post("/poses/batch-config")
async def get_batch_pose_config(pose_ids: List[int]):
    """批量获取姿势配置"""
    matcher = get_pose_matcher()
    results = {}
    for pose_id in pose_ids:
        config = matcher.get_pose_config(pose_id, {})
        if config:
            results[pose_id] = {
                "pose": config.pose,
                "reference_images": config.reference_images,
                "image_loras": config.image_loras,
                "video_loras": config.video_loras,
                "prompt_templates": config.prompt_templates
            }
    return results


@router.get("/poses/{pose_key}/thumbnail")
async def get_pose_thumbnail(pose_key: str, _: str = Depends(verify_api_key)):
    """获取姿势的缩略图URL（仅返回本地图片）"""
    try:
        conn = sqlite3.connect(str(POSE_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 只查询本地图片（/pose-files/ 开头）
        cursor.execute("""
            SELECT pri.image_url
            FROM pose_reference_images pri
            JOIN poses p ON pri.pose_id = p.id
            WHERE p.pose_key = ? AND pri.image_url LIKE '/pose-files/%'
            ORDER BY pri.is_default DESC
            LIMIT 1
        """, (pose_key,))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row:
            return {"url": row['image_url']}
        else:
            raise HTTPException(404, "No local thumbnail found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class WorkflowRecommendRequest(BaseModel):
    prompt: str
    pose_keys: List[str]


class LoraItem(BaseModel):
    lora_id: int
    lora_name: str
    weight: float


class WorkflowRecommendResponse(BaseModel):
    optimized_prompt: str
    reference_image: Optional[str]
    image_loras: List[LoraItem]
    image_prompt: str
    video_loras: List[LoraItem]
    video_prompt: str


@router.post("/poses/recommend-workflow", response_model=WorkflowRecommendResponse)
async def recommend_workflow(
    request: WorkflowRecommendRequest,
    _: str = Depends(verify_api_key)
):
    """
    根据prompt和姿势推荐完整的工作流配置

    合并多个姿势的配置，返回：
    - 优化的prompt
    - 推荐的参考图片
    - 推荐的image/video LORAs
    """
    try:
        matcher = get_pose_matcher()

        # 获取所有姿势的配置
        pose_configs = []
        for pose_key in request.pose_keys:
            poses = matcher.list_all_poses()
            pose = next((p for p in poses if p['pose_key'] == pose_key), None)

            if pose:
                config = matcher.get_pose_config(pose['id'], {})
                if config:
                    pose_configs.append(config)

        if not pose_configs:
            raise HTTPException(404, "未找到匹配的姿势配置")

        # 合并所有姿势的配置
        all_reference_images = []
        all_image_loras = []
        all_video_loras = []
        all_prompt_templates = []

        for config in pose_configs:
            all_reference_images.extend(config.reference_images)
            all_image_loras.extend(config.image_loras)
            all_video_loras.extend(config.video_loras)
            all_prompt_templates.extend(config.prompt_templates)

        # 选择首帧图片（优先选择默认图片）
        reference_image = None
        if all_reference_images:
            default_img = next((img for img in all_reference_images if img.get('is_default')), None)
            reference_image = (default_img or all_reference_images[0]).get('image_url')

        # 去重并选择LORA
        image_loras_dict = {}
        for lora in all_image_loras:
            lora_id = lora.get('lora_id')
            if lora_id and lora_id not in image_loras_dict:
                image_loras_dict[lora_id] = lora

        video_loras_dict = {}
        for lora in all_video_loras:
            lora_id = lora.get('lora_id')
            if lora_id and lora_id not in video_loras_dict:
                video_loras_dict[lora_id] = lora

        # 构建LORA列表
        image_loras = [
            LoraItem(
                lora_id=lora['lora_id'],
                lora_name=lora.get('lora_name', ''),
                weight=lora.get('recommended_weight', 1.0)
            )
            for lora in image_loras_dict.values()
        ][:5]

        video_loras = [
            LoraItem(
                lora_id=lora['lora_id'],
                lora_name=lora.get('lora_name', ''),
                weight=lora.get('recommended_weight', 1.0)
            )
            for lora in video_loras_dict.values()
        ][:5]

        # 优化prompt
        optimized_prompt = request.prompt
        if all_prompt_templates:
            template = all_prompt_templates[0].get('template', '')
            if template:
                optimized_prompt = f"{request.prompt}, {template}"

        image_prompt = optimized_prompt
        video_prompt = optimized_prompt

        return WorkflowRecommendResponse(
            optimized_prompt=optimized_prompt,
            reference_image=reference_image,
            image_loras=image_loras,
            image_prompt=image_prompt,
            video_loras=video_loras,
            video_prompt=video_prompt
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"推荐失败: {str(e)}")

