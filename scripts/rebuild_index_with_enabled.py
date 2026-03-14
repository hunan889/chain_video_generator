"""
重建索引（包含 enabled 字段）
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import asyncio
import pymysql
import json
from api.services.embedding_service import EmbeddingService

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


async def main():
    """主函数"""
    print("=" * 70)
    print("重建索引（包含 enabled 字段）")
    print("=" * 70)

    # 连接数据库
    print("\n1. 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    print("✓ 数据库连接成功")

    # 初始化embedding服务
    print("\n2. 初始化Embedding服务...")
    print("   (正在加载模型，请稍候...)")
    embedding_service = EmbeddingService(device='cpu')
    print("✓ Embedding服务已就绪 (使用CPU)")

    # 查询所有 enabled 的 LORA
    print("\n3. 查询LORA...")
    cursor.execute("""
        SELECT id, name, description, tags, trigger_words, search_keywords, enabled
        FROM lora_metadata
        WHERE enabled = 1 OR enabled = TRUE
        ORDER BY id
    """)
    loras = cursor.fetchall()
    print(f"✓ 找到 {len(loras)} 个已启用的 LORA")

    # 建立LORA索引
    print("\n4. 建立LORA索引...")
    success_count = 0
    error_count = 0

    for i, lora in enumerate(loras, 1):
        try:
            print(f"  [{i}/{len(loras)}] ✓ LORA #{lora['id']}: {lora['name']}")

            # 构建example_prompts
            example_prompts = []

            # 优先使用 search_keywords（按逗号分割，每个关键词单独建索引）
            if lora.get('search_keywords'):
                keywords = [k.strip() for k in lora['search_keywords'].split(',') if k.strip()]
                example_prompts.extend(keywords)
            # fallback 到 name
            elif lora['name']:
                example_prompts.append(lora['name'])

            if not example_prompts:
                print(f"    ⚠ 跳过: 没有可用的文本内容")
                continue

            # enabled 的 LORA 才会被查询出来，所以都是 True
            await embedding_service.index_lora(
                lora_id=lora['id'],
                example_prompts=example_prompts,
                enabled=True
            )

            success_count += 1

        except Exception as e:
            print(f"    ✗ 错误: {e}")
            error_count += 1

    print(f"\n✓ LORA索引完成: 成功 {success_count}, 失败 {error_count}")

    # 总结
    print("\n" + "=" * 70)
    print("索引构建完成")
    print("=" * 70)
    print(f"  已启用的 LORA 索引: {success_count} 个")
    print("=" * 70)

    cursor.close()
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
