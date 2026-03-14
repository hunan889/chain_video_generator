"""
批量分类LORA脚本
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import asyncio
import pymysql
import csv
from pathlib import Path
from api.services.lora_classifier import get_lora_classifier

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

OUTPUT_CSV = Path('/home/gime/soft/wan22-service/lora_classification_suggestions.csv')


async def classify_all_loras():
    """批量分类所有LORA"""
    print("=" * 70)
    print("LORA批量分类")
    print("=" * 70)

    # 连接数据库
    print("\n1. 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    print("✓ 数据库连接成功")

    # 查询所有LORA
    print("\n2. 查询LORA...")
    cursor.execute("""
        SELECT id, name, file, description, tags, trigger_words, category
        FROM lora_metadata
        ORDER BY id
    """)
    loras = cursor.fetchall()
    print(f"✓ 找到 {len(loras)} 个LORA")

    # 初始化分类器
    print("\n3. 初始化分类器...")
    classifier = get_lora_classifier()
    print("✓ 分类器已就绪")

    # 批量分类
    print("\n4. 开始分类...")
    results = []

    for i, lora in enumerate(loras, 1):
        print(f"\n  [{i}/{len(loras)}] 分类: {lora['name']}")

        classification = await classifier.classify(lora)

        result = {
            'id': lora['id'],
            'name': lora['name'],
            'file': lora['file'],
            'current_category': lora['category'] or '',
            'suggested_category': classification['category'] or '',
            'confidence': classification['confidence'],
            'reasoning': classification['reasoning'],
            'alternative_category': classification.get('alternative', {}).get('category', '') if classification.get('alternative') else '',
            'alternative_confidence': classification.get('alternative', {}).get('confidence', 0.0) if classification.get('alternative') else 0.0
        }

        results.append(result)

        print(f"    建议分类: {result['suggested_category']} (置信度: {result['confidence']:.2f})")
        print(f"    理由: {result['reasoning'][:80]}...")

    # 保存到CSV
    print(f"\n5. 保存结果到 {OUTPUT_CSV}...")
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'id', 'name', 'file', 'current_category', 'suggested_category',
            'confidence', 'reasoning', 'alternative_category', 'alternative_confidence'
        ])
        writer.writeheader()
        writer.writerows(results)

    print(f"✓ 结果已保存")

    # 统计
    print("\n" + "=" * 70)
    print("分类统计")
    print("=" * 70)

    from collections import Counter
    category_counts = Counter(r['suggested_category'] for r in results if r['suggested_category'])

    for category, count in category_counts.most_common():
        print(f"  {category:15s}: {count:3d} 个")

    high_confidence = sum(1 for r in results if r['confidence'] >= 0.8)
    medium_confidence = sum(1 for r in results if 0.5 <= r['confidence'] < 0.8)
    low_confidence = sum(1 for r in results if r['confidence'] < 0.5)

    print(f"\n置信度分布:")
    print(f"  高 (≥0.8): {high_confidence}")
    print(f"  中 (0.5-0.8): {medium_confidence}")
    print(f"  低 (<0.5): {low_confidence}")

    print("\n" + "=" * 70)
    print(f"✅ 完成！请查看 {OUTPUT_CSV} 并人工审核")
    print("=" * 70)

    cursor.close()
    conn.close()


if __name__ == "__main__":
    asyncio.run(classify_all_loras())
