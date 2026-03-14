"""
删除 disabled 的 LORA 向量索引
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import pymysql
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

print("=" * 70)
print("删除 disabled 的 LORA 向量索引")
print("=" * 70)

# 连接数据库
print("\n1. 查询 disabled 的 LORA...")
conn = pymysql.connect(**DB_CONFIG)
cursor = conn.cursor(pymysql.cursors.DictCursor)
cursor.execute("SELECT id, name FROM lora_metadata WHERE enabled = 0 OR enabled = FALSE")
disabled_loras = cursor.fetchall()
cursor.close()
conn.close()

print(f"✓ 找到 {len(disabled_loras)} 个 disabled 的 LORA")
for lora in disabled_loras:
    print(f"  - LORA #{lora['id']}: {lora['name']}")

if len(disabled_loras) == 0:
    print("\n没有需要删除的 LORA")
    sys.exit(0)

# 连接向量数据库
print("\n2. 连接向量数据库...")
connections.connect(
    alias="default",
    uri="https://in01-4423417d207b120.gcp-us-west1.vectordb.zillizcloud.com",
    token="cb7f6246ad28989f9ea2ea8b43a1bf0e263ebd44592e963772e603cb522caf53da7fccfb086a49e2147aa94f337f40975e61c1c5"
)
collection = Collection("wan22_lora_embeddings")
print("✓ 已连接")

# 删除向量索引
print("\n3. 删除向量索引...")
deleted_count = 0
for lora in disabled_loras:
    lora_id = lora['id']
    try:
        expr = f'lora_id == {lora_id}'
        collection.delete(expr)
        print(f"  ✓ 已删除 LORA #{lora_id}: {lora['name']}")
        deleted_count += 1
    except Exception as e:
        print(f"  ✗ 删除失败 LORA #{lora_id}: {e}")

print(f"\n✓ 删除完成: {deleted_count}/{len(disabled_loras)}")

# 验证
print("\n4. 验证...")
collection.load()
total = collection.num_entities
print(f"✓ 当前向量总数: {total}")

print("\n" + "=" * 70)
print("清理完成")
print("=" * 70)
