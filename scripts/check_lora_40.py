"""
检查 LORA #40 是否在向量数据库中
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

# 查询 LORA #40
results = collection.query(
    expr='lora_id == 40',
    output_fields=["lora_id", "prompt", "type", "enabled"],
    limit=10
)

print(f"找到 {len(results)} 条记录")
for r in results:
    print(f"  - lora_id: {r['lora_id']}, type: {r['type']}, enabled: {r.get('enabled', 'N/A')}")
    print(f"    prompt: {r['prompt']}")
