#!/usr/bin/env python3
"""
从搜索日志中自动发现新同义词
分析用户搜索行为，自动扩展同义词库
"""
import pymysql
from collections import defaultdict, Counter
from typing import Dict, List, Set
import json

DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


def analyze_search_patterns(min_searches: int = 5) -> Dict[str, List[str]]:
    """
    分析搜索日志，发现新的同义词模式

    逻辑：
    1. 如果多个不同的查询词都返回同一个LORA
    2. 说明这些查询词是同义词
    3. 自动建议添加到同义词库
    """

    # 这里需要搜索日志表，假设表结构：
    # CREATE TABLE search_logs (
    #   id INT PRIMARY KEY,
    #   query VARCHAR(200),
    #   result_lora_id INT,
    #   similarity FLOAT,
    #   created_at TIMESTAMP
    # )

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 检查是否有搜索日志表
    cursor.execute("SHOW TABLES LIKE 'search_logs'")
    if not cursor.fetchone():
        print("⚠️  搜索日志表不存在，创建建议：")
        print("""
CREATE TABLE search_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    query VARCHAR(200) NOT NULL,
    result_lora_id INT,
    similarity FLOAT,
    clicked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_query (query),
    INDEX idx_lora (result_lora_id),
    INDEX idx_created (created_at)
);
        """)
        cursor.close()
        conn.close()
        return {}

    # 分析：哪些查询词指向同一个LORA
    cursor.execute("""
        SELECT query, result_lora_id, COUNT(*) as search_count
        FROM search_logs
        WHERE similarity > 0.7
        GROUP BY query, result_lora_id
        HAVING search_count >= %s
        ORDER BY result_lora_id, search_count DESC
    """, (min_searches,))

    results = cursor.fetchall()

    # 按LORA分组查询词
    lora_queries = defaultdict(list)
    for row in results:
        lora_queries[row['result_lora_id']].append({
            'query': row['query'],
            'count': row['search_count']
        })

    # 生成同义词建议
    suggestions = {}

    for lora_id, queries in lora_queries.items():
        if len(queries) >= 2:  # 至少2个不同查询词
            # 获取LORA名称
            cursor.execute("SELECT name FROM lora_metadata WHERE id = %s", (lora_id,))
            lora = cursor.fetchone()

            if lora:
                query_terms = [q['query'] for q in queries]
                suggestions[lora['name']] = query_terms

    cursor.close()
    conn.close()

    return suggestions


def suggest_new_synonyms() -> Dict[str, Set[str]]:
    """
    基于搜索日志，建议新的同义词
    """
    print("📊 分析搜索日志...\n")

    patterns = analyze_search_patterns(min_searches=5)

    if not patterns:
        print("ℹ️  暂无足够的搜索数据")
        return {}

    print(f"发现 {len(patterns)} 个LORA有多个常见搜索词\n")

    # 加载现有同义词库
    from improve_lora_metadata import POSITION_SYNONYMS, BODY_SYNONYMS, ACTION_SYNONYMS

    existing_synonyms = {}
    for category in [POSITION_SYNONYMS, BODY_SYNONYMS, ACTION_SYNONYMS]:
        existing_synonyms.update(category)

    # 找出新的同义词
    new_suggestions = {}

    for lora_name, queries in patterns.items():
        # 提取LORA名称中的关键词
        name_lower = lora_name.lower()

        for key in existing_synonyms.keys():
            if key in name_lower:
                # 找出不在现有同义词库中的查询词
                existing = set(existing_synonyms[key])
                new_terms = set(queries) - existing - {key}

                if new_terms:
                    new_suggestions[key] = new_terms

                    print(f"💡 建议为 '{key}' 添加同义词:")
                    for term in new_terms:
                        print(f"   + '{term}'")
                    print()

    return new_suggestions


def auto_update_synonyms(dry_run: bool = True):
    """
    自动更新同义词库
    """
    suggestions = suggest_new_synonyms()

    if not suggestions:
        print("✅ 同义词库已是最新")
        return

    print(f"\n{'=' * 60}")
    print(f"发现 {len(suggestions)} 个可以改进的关键词\n")

    if dry_run:
        print("🔍 Dry run模式 - 建议的代码更新：\n")
        print("# 在 scripts/improve_lora_metadata.py 中添加：\n")

        for key, new_terms in suggestions.items():
            print(f"# 为 '{key}' 添加:")
            for term in new_terms:
                print(f"    '{term}',")
            print()
    else:
        print("⚠️  自动更新功能需要手动确认")
        print("请复制上面的建议，手动添加到 improve_lora_metadata.py")


def generate_synonym_report():
    """
    生成同义词覆盖率报告
    """
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 统计有多少LORA有丰富的标签
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN JSON_LENGTH(tags) >= 5 THEN 1 ELSE 0 END) as with_synonyms,
            SUM(CASE WHEN category IS NOT NULL THEN 1 ELSE 0 END) as with_category,
            SUM(CASE WHEN trigger_prompt IS NOT NULL THEN 1 ELSE 0 END) as with_trigger
        FROM lora_metadata
    """)

    stats = cursor.fetchone()

    print("\n📈 同义词覆盖率报告\n")
    print(f"总LORA数: {stats['total']}")
    print(f"有丰富标签(≥5个): {stats['with_synonyms']} ({stats['with_synonyms']/stats['total']*100:.1f}%)")
    print(f"有分类: {stats['with_category']} ({stats['with_category']/stats['total']*100:.1f}%)")
    print(f"有触发词: {stats['with_trigger']} ({stats['with_trigger']/stats['total']*100:.1f}%)")

    # 找出标签最少的LORA
    cursor.execute("""
        SELECT id, name, JSON_LENGTH(tags) as tag_count
        FROM lora_metadata
        WHERE JSON_LENGTH(tags) < 3
        ORDER BY tag_count
        LIMIT 10
    """)

    poor_loras = cursor.fetchall()

    if poor_loras:
        print(f"\n⚠️  标签最少的10个LORA:")
        for lora in poor_loras:
            print(f"   #{lora['id']} {lora['name']} - {lora['tag_count']}个标签")

    cursor.close()
    conn.close()


if __name__ == '__main__':
    import sys

    print("""
自动同义词发现工具

功能：
1. 分析用户搜索日志
2. 发现新的同义词模式
3. 自动建议更新同义词库

用法：
  python scripts/discover_synonyms.py              # 分析并建议新同义词
  python scripts/discover_synonyms.py --report     # 生成覆盖率报告
""")

    if '--report' in sys.argv:
        generate_synonym_report()
    else:
        auto_update_synonyms(dry_run=True)
