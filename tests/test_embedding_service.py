"""
测试Embedding服务
"""
import asyncio
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

from api.services.embedding_service import EmbeddingService


async def test_embedding_service():
    print("=" * 60)
    print("测试Embedding服务")
    print("=" * 60)

    # 初始化服务
    print("\n1. 初始化服务...")
    service = EmbeddingService(device='cpu')  # 先用CPU测试
    print("✓ 服务初始化成功")

    # 测试单个embedding
    print("\n2. 测试单个embedding生成...")
    text = "a woman in cowgirl position"
    embedding = await service.embed(text)
    print(f"✓ 文本: {text}")
    print(f"✓ Embedding维度: {len(embedding)}")
    print(f"✓ 前5个值: {embedding[:5]}")

    # 测试批量embedding
    print("\n3. 测试批量embedding生成...")
    texts = [
        "cowgirl position",
        "woman on top",
        "riding position",
        "doggy style"
    ]
    embeddings = await service.batch_embed(texts)
    print(f"✓ 生成了 {len(embeddings)} 个embeddings")

    # 测试相似度
    print("\n4. 测试相似度计算...")
    import numpy as np
    emb_array = np.array(embeddings)

    def cosine_sim(a, b):
        return np.dot(a, b)

    print(f"  'cowgirl' vs 'woman on top': {cosine_sim(emb_array[0], emb_array[1]):.3f}")
    print(f"  'cowgirl' vs 'riding': {cosine_sim(emb_array[0], emb_array[2]):.3f}")
    print(f"  'cowgirl' vs 'doggy': {cosine_sim(emb_array[0], emb_array[3]):.3f}")

    # 测试Zilliz连接
    print("\n5. 测试Zilliz连接...")
    stats = await service.get_stats()
    print(f"✓ Collection: {stats['collection']}")
    print(f"✓ 总索引数: {stats['total_embeddings']}")
    print(f"✓ 模型: {stats['model']}")

    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_embedding_service())
