"""
仅建立LORA索引
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
    print("建立LORA索引")
    print("=" * 70)

    # 连接数据库
    print("\n1. 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    print("✓ 数据库连接成功")

    # 查询所有 enabled 的 LORA
    print("\n2. 查询LORA...")
    cursor.execute("""
        SELECT id, name, description, tags, trigger_words, search_keywords, enabled
        FROM lora_metadata
        WHERE enabled = 1 OR enabled = TRUE
        ORDER BY id
    """)
    loras = cursor.fetchall()
    print(f"✓ 找到 {len(loras)} 个已启用的 LORA")

    if len(loras) == 0:
        print("⚠ 没有LORA，跳过索引构建")
        cursor.close()
        conn.close()
        return 0

    # 初始化embedding服务
    print("\n3. 初始化Embedding服务...")
    print("   (正在加载模型，请稍候...)")
    embedding_service = EmbeddingService(device='cuda:0')
    print("✓ Embedding服务已就绪 (使用cuda:0)")

    # 批量建立索引
    print("\n4. 建立索引...")
    success_count = 0
    error_count = 0

    for i, lora in enumerate(loras, 1):
        try:
            print(f"  [{i}/{len(loras)}] 索引LORA #{lora['id']}: {lora['name']}")

            # 构建example_prompts
            example_prompts = []

            # 优先使用 search_keywords
            if lora.get('search_keywords'):
                example_prompts.append(lora['search_keywords'])
            # fallback 到 name
            elif lora['name']:
                example_prompts.append(lora['name'])

            if not example_prompts:
                print(f"    ⚠ 跳过: 没有可用的文本内容")
                continue

            # 获取 enabled 状态
            enabled = bool(lora.get('enabled', True))

            await embedding_service.index_lora(
                lora_id=lora['id'],
                example_prompts=example_prompts,
                enabled=enabled
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
    print(f"  LORA索引: {success_count} 个")
    print("=" * 70)

    cursor.close()
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
