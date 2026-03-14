"""
重建资源索引
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import asyncio
import pymysql
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
    print("重建资源索引")
    print("=" * 70)

    # 连接数据库
    print("\n1. 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    print("✓ 数据库连接成功")

    # 查询所有有 search_keywords 的资源，并关联收藏状态
    print("\n2. 查询资源...")
    cursor.execute("""
        SELECT r.id, r.prompt, r.search_keywords,
               IF(f.resource_id IS NOT NULL, 1, 0) as is_favorited
        FROM resources r
        LEFT JOIN favorites f ON r.id = f.resource_id
        WHERE r.search_keywords IS NOT NULL AND r.search_keywords != ''
        ORDER BY r.id
    """)
    resources = cursor.fetchall()

    favorited_count = sum(1 for r in resources if r['is_favorited'])
    print(f"✓ 找到 {len(resources)} 个有 search_keywords 的资源")
    print(f"   - 已收藏: {favorited_count}")
    print(f"   - 未收藏: {len(resources) - favorited_count}")

    if len(resources) == 0:
        print("⚠ 没有资源需要索引")
        cursor.close()
        conn.close()
        return 0

    # 初始化embedding服务
    print("\n3. 初始化Embedding服务...")
    print("   (正在加载模型，请稍候...)")
    embedding_service = EmbeddingService(device='cpu')
    print("✓ Embedding服务已就绪 (使用CPU)")

    # 建立资源索引
    print("\n4. 建立资源索引...")
    success_count = 0
    error_count = 0

    for i, resource in enumerate(resources, 1):
        try:
            is_favorited = bool(resource['is_favorited'])
            status = "✓" if is_favorited else "○"
            print(f"  [{i}/{len(resources)}] {status} 索引资源 #{resource['id']}")

            # 使用 search_keywords 作为索引文本
            # 如果 search_keywords 是逗号分隔的关键词列表，保持原样（作为一个整体）
            # 因为资源的 search_keywords 通常是自然语言描述，不需要分割
            text = resource['search_keywords']

            await embedding_service.index_resource(
                resource_id=resource['id'],
                prompt=text,
                enabled=is_favorited
            )

            success_count += 1

        except Exception as e:
            print(f"    ✗ 错误: {e}")
            error_count += 1

    print(f"\n✓ 资源索引完成: 成功 {success_count}, 失败 {error_count}")

    # 总结
    print("\n" + "=" * 70)
    print("索引构建完成")
    print("=" * 70)
    print(f"  资源索引: {success_count} 个")
    print("=" * 70)

    cursor.close()
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
