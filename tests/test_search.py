"""
测试搜索功能
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import asyncio
from api.services.embedding_service_v2 import get_embedding_service_v2

async def test():
    print("初始化 embedding service...")
    service = get_embedding_service_v2()

    print("测试搜索...")
    results = await service.search_similar_loras_v2(
        query="doggy",
        lora_metadata={1: {"name": "test"}},
        top_k=5,
        only_enabled=True
    )

    print(f"搜索结果: {results}")

if __name__ == "__main__":
    asyncio.run(test())
