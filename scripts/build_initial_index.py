"""
建立初始索引 - 为收藏的资源和LORA建立embedding索引
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import asyncio
import pymysql
import json
from api.services.embedding_service import get_embedding_service

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


async def build_resource_index():
    """为收藏的资源建立索引"""
    print("\n" + "=" * 70)
    print("建立资源索引")
    print("=" * 70)

    # 连接数据库
    print("\n1. 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    print("✓ 数据库连接成功")

    # 查询收藏的资源
    print("\n2. 查询收藏资源...")
    cursor.execute("""
        SELECT DISTINCT r.id, r.prompt, r.url, r.search_keywords
        FROM favorites f
        JOIN resources r ON f.resource_id = r.id
        ORDER BY r.id
    """)
    resources = cursor.fetchall()
    print(f"✓ 找到 {len(resources)} 个收藏资源")

    if len(resources) == 0:
        print("⚠ 没有收藏资源，跳过索引构建")
        cursor.close()
        conn.close()
        return 0

    # 初始化embedding服务
    print("\n3. 初始化Embedding服务...")
    print("   (正在加载模型，请稍候...)")
    from api.services.embedding_service import EmbeddingService
    import os

    # 尝试使用GPU 0，如果失败则使用CPU
    device = 'cuda:0' if os.environ.get('CUDA_VISIBLE_DEVICES') != '' else 'cpu'
    try:
        embedding_service = EmbeddingService(device=device)
        print(f"✓ Embedding服务已就绪 (使用{device})")
    except Exception as e:
        print(f"   GPU加载失败: {e}")
        print("   切换到CPU...")
        embedding_service = EmbeddingService(device='cpu')
        print("✓ Embedding服务已就绪 (使用CPU)")

    # 批量建立索引
    print("\n4. 建立索引...")
    success_count = 0
    error_count = 0

    for i, resource in enumerate(resources, 1):
        try:
            # 构建索引文本
            index_text = None

            # 优先使用 search_keywords
            if resource.get('search_keywords'):
                index_text = resource['search_keywords']
            # fallback 到 prompt
            elif resource.get('prompt'):
                index_text = resource['prompt']
            # 最后 fallback 到 url
            elif resource.get('url'):
                # 从URL提取文件名
                filename = resource['url'].split('/')[-1].split('?')[0]
                index_text = filename

            if not index_text:
                print(f"  [{i}/{len(resources)}] 跳过资源 #{resource['id']}: 没有可用文本")
                continue

            print(f"  [{i}/{len(resources)}] 索引资源 #{resource['id']}: {index_text[:50]}...", flush=True)

            await embedding_service.index_resource(
                resource_id=resource['id'],
                prompt=index_text
            )

            print(f"      ✓ 完成", flush=True)

            success_count += 1

        except Exception as e:
            print(f"    ✗ 错误: {e}")
            error_count += 1

        # 每10个打印一次进度
        if i % 10 == 0:
            print(f"    进度: {i}/{len(resources)} ({i*100//len(resources)}%)")

    print(f"\n✓ 资源索引完成: 成功 {success_count}, 失败 {error_count}")

    cursor.close()
    conn.close()
    return success_count


async def build_lora_index():
    """为收藏的LORA建立索引"""
    print("\n" + "=" * 70)
    print("建立LORA索引")
    print("=" * 70)

    # 连接数据库
    print("\n1. 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    print("✓ 数据库连接成功")

    # 查询所有LORA（暂无lora_favorites表，索引全部）
    print("\n2. 查询LORA...")
    cursor.execute("""
        SELECT id, name, description, tags, trigger_words, search_keywords
        FROM lora_metadata
        ORDER BY id
    """)
    loras = cursor.fetchall()
    print(f"✓ 找到 {len(loras)} 个LORA")

    if len(loras) == 0:
        print("⚠ 没有收藏LORA，跳过索引构建")
        cursor.close()
        conn.close()
        return 0

    # 初始化embedding服务
    print("\n3. 初始化Embedding服务...")
    print("   (正在加载模型，请稍候...)")
    from api.services.embedding_service import EmbeddingService
    import os

    # 尝试使用GPU 0，如果失败则使用CPU
    device = 'cuda:0' if os.environ.get('CUDA_VISIBLE_DEVICES') != '' else 'cpu'
    try:
        embedding_service = EmbeddingService(device=device)
        print(f"✓ Embedding服务已就绪 (使用{device})")
    except Exception as e:
        print(f"   GPU加载失败: {e}")
        print("   切换到CPU...")
        embedding_service = EmbeddingService(device='cpu')
        print("✓ Embedding服务已就绪 (使用CPU)")

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

            await embedding_service.index_lora(
                lora_id=lora['id'],
                example_prompts=example_prompts
            )

            success_count += 1

        except Exception as e:
            print(f"    ✗ 错误: {e}")
            error_count += 1

    print(f"\n✓ LORA索引完成: 成功 {success_count}, 失败 {error_count}")

    cursor.close()
    conn.close()
    return success_count


async def main():
    """主函数"""
    print("=" * 70)
    print("建立初始索引")
    print("=" * 70)

    # 建立资源索引
    resource_count = await build_resource_index()

    # 建立LORA索引
    lora_count = await build_lora_index()

    # 总结
    print("\n" + "=" * 70)
    print("索引构建完成")
    print("=" * 70)
    print(f"  资源索引: {resource_count} 个")
    print(f"  LORA索引: {lora_count} 个")
    print(f"  总计: {resource_count + lora_count} 个")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
