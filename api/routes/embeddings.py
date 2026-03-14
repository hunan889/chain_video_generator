"""
索引管理API路由
"""
import logging
import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.middleware.auth import verify_api_key
from api.services.embedding_service import get_embedding_service
import pymysql

logger = logging.getLogger(__name__)
router = APIRouter()

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

# 任务存储
_index_tasks = {}


class IndexStatsResponse(BaseModel):
    total_count: int
    resource_count: int
    lora_count: int
    model_name: str
    dimension: int


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str  # running, completed, failed
    progress: Optional[float] = None
    total: Optional[int] = None
    processed: Optional[int] = None
    error: Optional[str] = None


class RebuildTaskResponse(BaseModel):
    task_id: str
    status: str


@router.get("/admin/embeddings/stats", response_model=IndexStatsResponse)
async def get_embedding_stats(_=Depends(verify_api_key)):
    """获取索引统计信息"""
    try:
        embedding_service = get_embedding_service()

        # 查询Zilliz统计
        stats = await embedding_service.get_stats()

        return IndexStatsResponse(
            total_count=stats.get('total_count', 0),
            resource_count=stats.get('resource_count', 0),
            lora_count=stats.get('lora_count', 0),
            model_name='BGE-Large-ZH-v1.5',
            dimension=1024
        )
    except Exception as e:
        logger.error(f"Failed to get embedding stats: {e}")
        raise HTTPException(500, f"获取统计失败: {str(e)}")


@router.post("/admin/embeddings/rebuild-favorites", response_model=RebuildTaskResponse)
async def rebuild_favorites_index(_=Depends(verify_api_key)):
    """重建收藏资源索引"""
    task_id = uuid.uuid4().hex[:8]
    _index_tasks[task_id] = {
        'status': 'running',
        'progress': 0.0,
        'total': 0,
        'processed': 0,
        'error': None
    }

    async def _rebuild():
        try:
            conn = pymysql.connect(**DB_CONFIG)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询收藏资源
            cursor.execute("""
                SELECT DISTINCT r.id, r.prompt
                FROM favorites f
                JOIN resources r ON f.resource_id = r.id
                WHERE r.prompt IS NOT NULL AND r.prompt != ''
                ORDER BY r.id
            """)
            resources = cursor.fetchall()
            cursor.close()
            conn.close()

            total = len(resources)
            _index_tasks[task_id]['total'] = total

            if total == 0:
                _index_tasks[task_id]['status'] = 'completed'
                return

            embedding_service = get_embedding_service()

            for i, resource in enumerate(resources):
                try:
                    # 截断过长的prompt
                    prompt = resource['prompt']
                    if len(prompt) > 2000:
                        prompt = prompt[:2000]

                    await embedding_service.index_resource(
                        resource_id=resource['id'],
                        prompt=prompt
                    )

                    _index_tasks[task_id]['processed'] = i + 1
                    _index_tasks[task_id]['progress'] = (i + 1) / total

                except Exception as e:
                    logger.warning(f"Failed to index resource #{resource['id']}: {e}")

            _index_tasks[task_id]['status'] = 'completed'

        except Exception as e:
            logger.error(f"Rebuild favorites task failed: {e}")
            _index_tasks[task_id]['status'] = 'failed'
            _index_tasks[task_id]['error'] = str(e)

    asyncio.create_task(_rebuild())

    return RebuildTaskResponse(task_id=task_id, status='running')


@router.post("/admin/embeddings/rebuild-loras", response_model=RebuildTaskResponse)
async def rebuild_loras_index(_=Depends(verify_api_key)):
    """重建LORA索引"""
    task_id = uuid.uuid4().hex[:8]
    _index_tasks[task_id] = {
        'status': 'running',
        'progress': 0.0,
        'total': 0,
        'processed': 0,
        'error': None
    }

    async def _rebuild():
        try:
            conn = pymysql.connect(**DB_CONFIG)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询所有启用的LORA
            cursor.execute("""
                SELECT id, name, description, tags, trigger_words, trigger_prompt
                FROM lora_metadata
                WHERE enabled = TRUE OR enabled IS NULL
                ORDER BY id
            """)
            loras = cursor.fetchall()
            cursor.close()
            conn.close()

            total = len(loras)
            _index_tasks[task_id]['total'] = total

            if total == 0:
                _index_tasks[task_id]['status'] = 'completed'
                return

            embedding_service = get_embedding_service()
            import json

            for i, lora in enumerate(loras):
                try:
                    # 构建example_prompts
                    example_prompts = []

                    # 优先使用trigger_prompt
                    if lora.get('trigger_prompt'):
                        example_prompts.append(lora['trigger_prompt'])

                    if lora['description']:
                        example_prompts.append(lora['description'])

                    trigger_words = lora.get('trigger_words')
                    if isinstance(trigger_words, str):
                        try:
                            trigger_words = json.loads(trigger_words)
                        except:
                            trigger_words = []
                    if trigger_words:
                        example_prompts.extend(trigger_words)

                    tags = lora.get('tags')
                    if isinstance(tags, str):
                        try:
                            tags = json.loads(tags)
                        except:
                            tags = []
                    if tags:
                        example_prompts.extend(tags)

                    if example_prompts:
                        await embedding_service.index_lora(
                            lora_id=lora['id'],
                            example_prompts=example_prompts
                        )

                    _index_tasks[task_id]['processed'] = i + 1
                    _index_tasks[task_id]['progress'] = (i + 1) / total

                except Exception as e:
                    logger.warning(f"Failed to index LORA #{lora['id']}: {e}")

            _index_tasks[task_id]['status'] = 'completed'

        except Exception as e:
            logger.error(f"Rebuild LORAs task failed: {e}")
            _index_tasks[task_id]['status'] = 'failed'
            _index_tasks[task_id]['error'] = str(e)

    asyncio.create_task(_rebuild())

    return RebuildTaskResponse(task_id=task_id, status='running')


@router.get("/admin/embeddings/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, _=Depends(verify_api_key)):
    """查询任务状态"""
    if task_id not in _index_tasks:
        raise HTTPException(404, "Task not found")

    task = _index_tasks[task_id]

    return TaskStatusResponse(
        task_id=task_id,
        status=task['status'],
        progress=task.get('progress'),
        total=task.get('total'),
        processed=task.get('processed'),
        error=task.get('error')
    )


@router.delete("/admin/embeddings/clear-all")
async def clear_all_embeddings(_=Depends(verify_api_key)):
    """清空所有索引"""
    try:
        embedding_service = get_embedding_service()
        await embedding_service.clear_all()

        return {"success": True, "message": "所有索引已清空"}
    except Exception as e:
        logger.error(f"Failed to clear embeddings: {e}")
        raise HTTPException(500, f"清空失败: {str(e)}")
