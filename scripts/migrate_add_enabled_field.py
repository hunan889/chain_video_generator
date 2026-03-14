"""
迁移脚本：添加 enabled 字段到向量数据库
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

from pymilvus import connections, Collection, utility, CollectionSchema, FieldSchema, DataType
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate(auto_confirm=False):
    """执行迁移"""
    # 连接Zilliz
    connections.connect(
        alias="default",
        uri="https://in01-4423417d207b120.gcp-us-west1.vectordb.zillizcloud.com",
        token="cb7f6246ad28989f9ea2ea8b43a1bf0e263ebd44592e963772e603cb522caf53da7fccfb086a49e2147aa94f337f40975e61c1c5"
    )
    logger.info("已连接到Zilliz")

    collection_name = "wan22_lora_embeddings"

    # 检查旧collection是否存在
    if utility.has_collection(collection_name):
        logger.info(f"发现旧collection: {collection_name}")
        old_collection = Collection(collection_name)

        # 获取统计信息
        old_collection.load()
        total_count = old_collection.num_entities
        logger.info(f"旧collection包含 {total_count} 条记录")

        # 删除旧collection
        logger.warning(f"准备删除旧collection: {collection_name}")
        if not auto_confirm:
            response = input("确认删除？(yes/no): ")
            if response.lower() != 'yes':
                logger.info("取消迁移")
                return
        else:
            logger.info("自动确认删除")

        utility.drop_collection(collection_name)
        logger.info(f"已删除旧collection: {collection_name}")

    # 创建新collection（包含enabled字段）
    logger.info("创建新collection...")
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),
        FieldSchema(name="resource_id", dtype=DataType.INT64, nullable=True),
        FieldSchema(name="lora_id", dtype=DataType.INT64, nullable=True),
        FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=20),
        FieldSchema(name="prompt", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="enabled", dtype=DataType.BOOL, default_value=True),  # 新增字段
        FieldSchema(name="created_at", dtype=DataType.INT64),
    ]

    schema = CollectionSchema(fields, description="LORA recommendation embeddings with enabled field")
    collection = Collection(collection_name, schema)

    # 创建索引
    index_params = {
        "metric_type": "IP",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128}
    }
    collection.create_index("embedding", index_params)
    logger.info(f"已创建新collection: {collection_name}")

    logger.info("✓ 迁移完成！")
    logger.info("⚠ 请运行 rebuild_index_with_enabled.py 重建索引")


if __name__ == "__main__":
    auto_confirm = "--yes" in sys.argv or "-y" in sys.argv
    migrate(auto_confirm)
