#!/usr/bin/env python3
"""
添加 LORA 收藏字段到 favorites 表
"""
import sys
import pymysql

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

def main():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        # 添加 lora_id 和 lora_type 字段
        print("Adding lora_id and lora_type columns...")
        try:
            cursor.execute("""
                ALTER TABLE favorites
                ADD COLUMN lora_id INT NULL AFTER resource_path,
                ADD COLUMN lora_type VARCHAR(20) NULL AFTER lora_id
            """)
            print("✓ Added lora_id and lora_type columns")
        except pymysql.err.OperationalError as e:
            if "Duplicate column name" in str(e):
                print("✓ Columns already exist")
            else:
                raise

        # 添加唯一索引
        print("\nAdding unique index for LORA favorites...")
        try:
            cursor.execute("""
                ALTER TABLE favorites
                ADD UNIQUE KEY unique_user_lora (user_id, lora_id, lora_type)
            """)
            print("✓ Added unique_user_lora index")
        except pymysql.err.OperationalError as e:
            if "Duplicate key name" in str(e):
                print("✓ Index already exists")
            else:
                raise

        conn.commit()

        # 验证表结构
        print("\n✓ Current favorites table structure:")
        cursor.execute("DESCRIBE favorites")
        columns = cursor.fetchall()
        for col in columns:
            print(f"  {col[0]}: {col[1]}")

        print("\n✓ Database schema updated successfully!")

    except Exception as e:
        print(f"\n✗ Error: {e}")
        conn.rollback()
        return 1
    finally:
        cursor.close()
        conn.close()

    return 0

if __name__ == "__main__":
    sys.exit(main())
