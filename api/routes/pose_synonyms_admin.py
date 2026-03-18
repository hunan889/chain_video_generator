"""
姿势同义词管理API
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict
from api.middleware.auth import verify_api_key
from api.services.pose_synonyms import POSE_SYNONYMS
import json
from pathlib import Path

router = APIRouter(prefix="/api/v1/admin", tags=["pose_synonyms"])

PROJECT_ROOT = Path(__file__).parent.parent.parent
SYNONYMS_FILE = PROJECT_ROOT / "api" / "services" / "pose_synonyms.py"


class SynonymUpdateRequest(BaseModel):
    """同义词更新请求"""
    pose_key: str
    synonyms: List[str]


@router.get("/pose-synonyms")
async def get_all_synonyms(_: str = Depends(verify_api_key)):
    """获取所有姿势的同义词配置"""
    return {"synonyms": POSE_SYNONYMS}


@router.put("/pose-synonyms/{pose_key}")
async def update_synonyms(
    pose_key: str,
    request: SynonymUpdateRequest,
    _: str = Depends(verify_api_key)
):
    """更新指定姿势的同义词"""
    # 更新内存中的配置
    POSE_SYNONYMS[pose_key] = request.synonyms

    # 保存到文件
    _save_synonyms_to_file()

    # 重新初始化推荐器（清除缓存）
    from api.services import pose_recommender
    if pose_recommender._recommender_instance:
        pose_recommender._recommender_instance.initialize()

    return {"success": True, "pose_key": pose_key, "synonyms": request.synonyms}


def _save_synonyms_to_file():
    """保存同义词配置到文件"""
    content = '''"""
姿势同义词词典
"""

# 姿势同义词映射
POSE_SYNONYMS = '''

    content += json.dumps(POSE_SYNONYMS, ensure_ascii=False, indent=4)

    content += '''


def get_synonyms(pose_key: str) -> list:
    """获取姿势的同义词列表"""
    return POSE_SYNONYMS.get(pose_key, [])


def expand_query(query: str) -> str:
    """扩展查询，添加同义词"""
    query_lower = query.lower()
    expanded_terms = [query_lower]

    # 检查是否包含同义词
    for pose_key, synonyms in POSE_SYNONYMS.items():
        for synonym in synonyms:
            if synonym.lower() in query_lower:
                # 添加姿势key和其他同义词
                expanded_terms.append(pose_key)
                expanded_terms.extend(synonyms)
                break

    return ' '.join(set(expanded_terms))
'''

    with open(SYNONYMS_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
