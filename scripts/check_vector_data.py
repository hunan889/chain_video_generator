"""
检查向量数据库中的数据
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

from pymilvus import connections, Collection

# 连接Zilliz
connections.connect(
    alias="default",
    uri="https://in01-4423417d207b120.gcp-us-west1.vectordb.zillizcloud.com",
    token="cb7f6246ad28989f9ea2ea8b43a1bf0e263ebd44592e963772e603cb522caf53da7fccfb086a49e2147aa94f337f40975e61c1c5"
)

collection = Collection("wan22_lora_embeddings")
collection.load()

# 统计各类型数据
total = collection.num_entities
print(f"总记录数: {total}")

# 查询各类型数量
try:
    lora_results = collection.query(
        expr='type == "lora"',
        output_fields=["type"],
        limit=16384
    )
    print(f"LORA 记录数: {len(lora_results)}")

    resource_results = collection.query(
        expr='type == "resource"',
        output_fields=["type"],
        limit=16384
    )
    print(f"Resource 记录数: {len(resource_results)}")
except Exception as e:
    print(f"查询失败: {e}")
