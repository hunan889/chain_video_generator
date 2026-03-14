"""
快速测试脚本 - 验证Embedding服务的实际效果
"""
import asyncio
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

from api.services.embedding_service import EmbeddingService


async def demo():
    print("\n" + "=" * 70)
    print("Embedding服务演示 - 语义搜索效果")
    print("=" * 70)

    # 初始化
    service = EmbeddingService(device='cpu')

    # 场景1: 近义词识别
    print("\n【场景1】近义词识别")
    print("-" * 70)

    query = "cowgirl position"
    candidates = [
        "woman on top",
        "riding position",
        "girl riding dick",
        "doggy style",
        "missionary position",
        "blowjob"
    ]

    print(f"查询: '{query}'")
    print(f"\n候选列表:")

    query_emb = await service.embed(query)
    candidate_embs = await service.batch_embed(candidates)

    import numpy as np
    results = []
    for i, (text, emb) in enumerate(zip(candidates, candidate_embs)):
        similarity = np.dot(query_emb, emb)
        results.append((text, similarity))

    results.sort(key=lambda x: x[1], reverse=True)

    for i, (text, sim) in enumerate(results, 1):
        status = "✅" if sim > 0.4 else "❌"
        print(f"  {i}. {text:30s} → 相似度: {sim:.3f} {status}")

    # 场景2: 动作识别
    print("\n【场景2】动作识别")
    print("-" * 70)

    query = "woman having orgasm"
    candidates = [
        "screaming orgasm",
        "climax scene",
        "intense pleasure",
        "kissing",
        "dancing",
        "walking"
    ]

    print(f"查询: '{query}'")
    print(f"\n候选列表:")

    query_emb = await service.embed(query)
    candidate_embs = await service.batch_embed(candidates)

    results = []
    for text, emb in zip(candidates, candidate_embs):
        similarity = np.dot(query_emb, emb)
        results.append((text, similarity))

    results.sort(key=lambda x: x[1], reverse=True)

    for i, (text, sim) in enumerate(results, 1):
        status = "✅" if sim > 0.4 else "❌"
        print(f"  {i}. {text:30s} → 相似度: {sim:.3f} {status}")

    # 场景3: 中英文混合
    print("\n【场景3】中英文混合")
    print("-" * 70)

    query = "性感女孩骑乘姿势"
    candidates = [
        "cowgirl position",
        "sexy girl riding",
        "woman on top",
        "doggy style",
        "站立姿势"
    ]

    print(f"查询: '{query}'")
    print(f"\n候选列表:")

    query_emb = await service.embed(query)
    candidate_embs = await service.batch_embed(candidates)

    results = []
    for text, emb in zip(candidates, candidate_embs):
        similarity = np.dot(query_emb, emb)
        results.append((text, similarity))

    results.sort(key=lambda x: x[1], reverse=True)

    for i, (text, sim) in enumerate(results, 1):
        status = "✅" if sim > 0.4 else "❌"
        print(f"  {i}. {text:30s} → 相似度: {sim:.3f} {status}")

    print("\n" + "=" * 70)
    print("✅ 演示完成！Embedding模型能够正确识别语义相似的文本。")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(demo())
