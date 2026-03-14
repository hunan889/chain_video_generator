"""
测试搜索 "on all fours"
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import asyncio
from api.services.embedding_service_v2 import get_embedding_service_v2

async def test():
    print("测试搜索: 'on all fours'")

    service = get_embedding_service_v2()

    # 获取 LORA 元数据
    import pymysql
    DB_CONFIG = {
        'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
        'port': 20603,
        'user': 'user_soga',
        'password': '1IvO@*#68',
        'database': 'tudou_soga',
        'charset': 'utf8mb4'
    }

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT id, name FROM lora_metadata")
    lora_metadata = {row['id']: {"name": row['name']} for row in cursor.fetchall()}
    cursor.close()
    conn.close()

    # 搜索
    results = await service.search_similar_loras_v2(
        query="on all fours",
        lora_metadata=lora_metadata,
        top_k=10,
        min_similarity=0.3
    )

    print(f"\n找到 {len(results)} 个结果:")
    for r in results:
        print(f"  - LORA #{r['lora_id']}: {lora_metadata[r['lora_id']]['name']}")
        print(f"    相似度: {r['similarity']:.4f}")
        print(f"    标签相似度: {r.get('tag_similarity', 0):.4f}")
        print(f"    名称相似度: {r.get('name_similarity', 0):.4f}")

if __name__ == "__main__":
    asyncio.run(test())
