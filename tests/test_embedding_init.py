"""
快速测试embedding服务初始化
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

print("开始测试...")
print("1. 导入模块...")
from api.services.embedding_service import EmbeddingService

print("2. 初始化服务 (GPU 0)...")
service = EmbeddingService(device='cuda:0')

print("3. 测试embedding生成...")
import asyncio

async def test():
    embedding = await service.embed("test prompt")
    print(f"✓ Embedding生成成功，维度: {len(embedding)}")

asyncio.run(test())
print("✓ 测试完成")
