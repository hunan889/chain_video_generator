"""
工作流推荐API
根据prompt和姿势推荐完整的工作流配置
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from api.middleware.auth import verify_api_key
from api.services.pose_matcher import get_pose_matcher
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


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


@router.post("/recommend/workflow", response_model=WorkflowRecommendResponse)
async def recommend_workflow(
    request: WorkflowRecommendRequest,
    _: str = Depends(verify_api_key)
):
    """
    工作流推荐API

    根据用户输入的prompt和选择的姿势，返回完整的推荐配置
    """
    try:
        matcher = get_pose_matcher()

        # 获取所有姿势的配置
        pose_configs = []
        for pose_key in request.pose_keys:
            # 通过pose_key查找pose_id
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
        ][:5]  # 最多5个

        video_loras = [
            LoraItem(
                lora_id=lora['lora_id'],
                lora_name=lora.get('lora_name', ''),
                weight=lora.get('recommended_weight', 1.0)
            )
            for lora in video_loras_dict.values()
        ][:5]  # 最多5个

        # 优化prompt（简单实现：添加姿势相关的提示词）
        optimized_prompt = request.prompt
        if all_prompt_templates:
            template = all_prompt_templates[0].get('template', '')
            if template:
                optimized_prompt = f"{request.prompt}, {template}"

        # 生成图片和视频的prompt
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
        logger.error(f"工作流推荐失败: {e}")
        raise HTTPException(500, f"推荐失败: {str(e)}")
