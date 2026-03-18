#!/usr/bin/env python3
"""
添加LORA排序字段
"""
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "wan22.db"


def migrate():
    """添加sort_order字段到pose_loras表"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    try:
        # 检查字段是否已存在
        cursor.execute("PRAGMA table_info(pose_loras)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'sort_order' in columns:
            print("✓ sort_order字段已存在")
        else:
            # 添加sort_order字段
            cursor.execute("""
                ALTER TABLE pose_loras
                ADD COLUMN sort_order INTEGER DEFAULT 0
            """)
            print("✓ 添加sort_order字段成功")

        # 为现有数据设置sort_order（按id顺序）
        cursor.execute("""
            UPDATE pose_loras
            SET sort_order = id
            WHERE sort_order = 0
        """)

        conn.commit()
        print(f"✓ 更新了 {cursor.rowcount} 条记录的sort_order")

        # 验证
        cursor.execute("SELECT COUNT(*) FROM pose_loras WHERE sort_order > 0")
        count = cursor.fetchone()[0]
        print(f"✓ 验证成功：{count} 条LORA记录有sort_order")

    except Exception as e:
        conn.rollback()
        print(f"✗ 迁移失败: {e}")
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    print("开始数据库迁移...")
    migrate()
    print("迁移完成！")
