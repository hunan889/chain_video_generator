"""
更新 LORA 的 enabled 状态（添加或删除向量索引）
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import asyncio
import pymysql
from api.services.embedding_service import EmbeddingService
from pymilvus import connections, Collection

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


async def enable_lora(lora_id: int):
    """启用 LORA - 添加向量索引"""
    print(f"\n启用 LORA #{lora_id}...")

    # 查询 LORA 信息
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("""
        SELECT id, name, search_keywords
        FROM lora_metadata
        WHERE id = %s
    """, (lora_id,))
    lora = cursor.fetchone()
    cursor.close()
    conn.close()

    if not lora:
        print(f"✗ LORA #{lora_id} 不存在")
        return False

    print(f"  LORA: {lora['name']}")

    # 构建 example_prompts（按逗号分割关键词，每个关键词单独建索引）
    example_prompts = []
    if lora.get('search_keywords'):
        keywords = [k.strip() for k in lora['search_keywords'].split(',') if k.strip()]
        example_prompts.extend(keywords)
    elif lora['name']:
        example_prompts.append(lora['name'])

    if not example_prompts:
        print(f"  ✗ 没有可用的文本内容")
        return False

    # 建立向量索引
    embedding_service = EmbeddingService(device='cpu')
    await embedding_service.index_lora(
        lora_id=lora['id'],
        example_prompts=example_prompts,
        enabled=True
    )

    print(f"  ✓ 已添加向量索引")
    return True


async def disable_lora(lora_id: int):
    """禁用 LORA - 删除向量索引"""
    print(f"\n禁用 LORA #{lora_id}...")

    # 查询 LORA 名称
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT name FROM lora_metadata WHERE id = %s", (lora_id,))
    lora = cursor.fetchone()
    cursor.close()
    conn.close()

    if not lora:
        print(f"✗ LORA #{lora_id} 不存在")
        return False

    print(f"  LORA: {lora['name']}")

    # 连接向量数据库
    connections.connect(
        alias="default",
        uri="https://in01-4423417d207b120.gcp-us-west1.vectordb.zillizcloud.com",
        token="cb7f6246ad28989f9ea2ea8b43a1bf0e263ebd44592e963772e603cb522caf53da7fccfb086a49e2147aa94f337f40975e61c1c5"
    )
    collection = Collection("wan22_lora_embeddings")

    # 删除向量索引
    expr = f'lora_id == {lora_id}'
    collection.delete(expr)

    print(f"  ✓ 已删除向量索引")
    return True


async def main():
    """主函数"""
    if len(sys.argv) < 3:
        print("用法:")
        print("  python update_lora_enabled.py enable <lora_id>   # 启用 LORA")
        print("  python update_lora_enabled.py disable <lora_id>  # 禁用 LORA")
        sys.exit(1)

    action = sys.argv[1]
    lora_id = int(sys.argv[2])

    print("=" * 70)
    print(f"更新 LORA #{lora_id} 的 enabled 状态")
    print("=" * 70)

    if action == "enable":
        success = await enable_lora(lora_id)
    elif action == "disable":
        success = await disable_lora(lora_id)
    else:
        print(f"✗ 未知操作: {action}")
        sys.exit(1)

    if success:
        print("\n✓ 操作成功")
    else:
        print("\n✗ 操作失败")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
