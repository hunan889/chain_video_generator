#!/usr/bin/env python3
"""
测试名称相似度加权方案
"""
import asyncio
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

from api.services.embedding_service_v2 import EmbeddingServiceV2


async def test_name_similarity():
    """测试名称相似度计算"""
    service = EmbeddingServiceV2(device='cpu')

    query = "from behind"

    test_cases = [
        "wan_22_doggy_by_mq_lab",
        "from_behind_style",
        "from_behind_pov",
        "doggy_style",
        "cowgirl",
    ]

    print(f'查询: "{query}"\n')
    print("名称相似度测试:\n")

    for lora_name in test_cases:
        name_sim = service.calculate_name_similarity(query, lora_name)
        print(f"  {lora_name:30s} → {name_sim:.4f}")

    print("\n" + "=" * 60)


async def test_weighted_search():
    """测试加权搜索"""
    service = EmbeddingServiceV2(device='cpu')

    # 模拟场景
    query = "from behind"

    # 模拟LORA元数据
    lora_metadata = {
        40: {"name": "wan_22_doggy_by_mq_lab"},
        53: {"name": "wan_22_doggy_by_mq_lab"},
        99: {"name": "from_behind_style"},  # 假设的LORA
    }

    print(f'\n查询: "{query}"\n')
    print("=" * 60)

    # 测试不同的name_weight
    for name_weight in [0.0, 0.2, 0.3, 0.5]:
        print(f"\n名称权重 = {name_weight}")
        print("-" * 60)

        # 模拟搜索结果
        # 假设所有LORA的标签相似度都是1.0（都有"from behind"标签）
        mock_results = [
            {
                "lora_id": 40,
                "tag_similarity": 1.0,
                "name": "wan_22_doggy_by_mq_lab"
            },
            {
                "lora_id": 99,
                "tag_similarity": 1.0,
                "name": "from_behind_style"
            },
        ]

        # 计算综合分数
        for item in mock_results:
            tag_sim = item["tag_similarity"]
            name_sim = service.calculate_name_similarity(query, item["name"])
            final_score = tag_sim * (1 - name_weight) + name_sim * name_weight

            print(f"\n  {item['name']}")
            print(f"    标签相似度: {tag_sim:.4f}")
            print(f"    名称相似度: {name_sim:.4f}")
            print(f"    综合分数:   {final_score:.4f}")

        # 排序
        for item in mock_results:
            name_sim = service.calculate_name_similarity(query, item["name"])
            item["final_score"] = item["tag_similarity"] * (1 - name_weight) + name_sim * name_weight

        mock_results.sort(key=lambda x: x["final_score"], reverse=True)

        print(f"\n  🏆 排序结果:")
        for i, item in enumerate(mock_results, 1):
            print(f"    {i}. {item['name']} (分数: {item['final_score']:.4f})")


if __name__ == '__main__':
    print("=" * 60)
    print("名称相似度加权方案测试")
    print("=" * 60)

    asyncio.run(test_name_similarity())
    asyncio.run(test_weighted_search())

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
