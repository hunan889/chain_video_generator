"""
同步LORA元数据从loras.yaml到数据库
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import yaml
import pymysql
import json
from pathlib import Path

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

LORAS_PATH = Path('/home/gime/soft/wan22-service/config/loras.yaml')


def load_loras_yaml():
    """从loras.yaml加载数据"""
    with open(LORAS_PATH, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return data.get('loras', [])


def detect_noise_stage(file_name):
    """根据文件名检测noise stage"""
    file_lower = file_name.lower()
    if 'high' in file_lower or 'highnoise' in file_lower:
        return 'high'
    elif 'low' in file_lower or 'lownoise' in file_lower:
        return 'low'
    else:
        return 'single'


def detect_mode(lora):
    """检测LORA模式（I2V/T2V/both）"""
    name = lora.get('name', '').lower()
    desc = lora.get('description', '').lower()
    file = lora.get('file', '').lower()

    combined = f"{name} {desc} {file}"

    if 'i2v' in combined and 't2v' not in combined:
        return 'I2V'
    elif 't2v' in combined and 'i2v' not in combined:
        return 'T2V'
    else:
        return 'both'


def sync_loras_to_db():
    """同步LORA到数据库"""
    print("=" * 70)
    print("同步LORA元数据到数据库")
    print("=" * 70)

    # 加载YAML数据
    print("\n1. 加载loras.yaml...")
    loras = load_loras_yaml()
    print(f"✓ 加载了 {len(loras)} 个LORA")

    # 连接数据库
    print("\n2. 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    print("✓ 数据库连接成功")

    # 同步数据
    print("\n3. 同步数据...")
    inserted = 0
    updated = 0
    skipped = 0

    for lora in loras:
        try:
            name = lora.get('name', '')
            file = lora.get('file', '')

            if not name or not file:
                print(f"  ⚠ 跳过无效LORA: {lora}")
                skipped += 1
                continue

            # 检测noise_stage和mode
            noise_stage = detect_noise_stage(file)
            mode = detect_mode(lora)

            # 检查civitai_id是否已被使用
            civitai_id = lora.get('civitai_id')
            if civitai_id:
                cursor.execute("SELECT file FROM lora_metadata WHERE civitai_id = %s", (civitai_id,))
                existing_civitai = cursor.fetchone()
                if existing_civitai and existing_civitai[0] != file:
                    print(f"  ⚠ civitai_id {civitai_id} 已被 {existing_civitai[0]} 使用，设为NULL")
                    civitai_id = None

            # 准备数据
            data = {
                'name': name,
                'file': file,
                'category': None,  # 稍后通过AI分类
                'modifiers': json.dumps({'default_strength': lora.get('default_strength', 0.8)}),
                'trigger_words': json.dumps(lora.get('trigger_words', [])),
                'civitai_id': civitai_id,
                'civitai_version_id': lora.get('civitai_version_id'),
                'preview_url': lora.get('preview_url'),
                'description': lora.get('description', ''),
                'tags': json.dumps(lora.get('tags', [])),
                'mode': mode,
                'noise_stage': noise_stage,
                'quality_score': None,
                'download_count': 0
            }

            # 检查是否已存在
            cursor.execute("SELECT id FROM lora_metadata WHERE file = %s", (file,))
            existing = cursor.fetchone()

            if existing:
                # 更新
                cursor.execute("""
                    UPDATE lora_metadata SET
                        name = %s,
                        modifiers = %s,
                        trigger_words = %s,
                        civitai_id = %s,
                        civitai_version_id = %s,
                        preview_url = %s,
                        description = %s,
                        tags = %s,
                        mode = %s,
                        noise_stage = %s
                    WHERE file = %s
                """, (
                    data['name'], data['modifiers'], data['trigger_words'],
                    data['civitai_id'], data['civitai_version_id'],
                    data['preview_url'], data['description'], data['tags'],
                    data['mode'], data['noise_stage'], file
                ))
                updated += 1
                print(f"  ↻ 更新: {name}")
            else:
                # 插入
                cursor.execute("""
                    INSERT INTO lora_metadata (
                        name, file, category, modifiers, trigger_words,
                        civitai_id, civitai_version_id, preview_url, description,
                        tags, mode, noise_stage, quality_score, download_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    data['name'], data['file'], data['category'], data['modifiers'],
                    data['trigger_words'], data['civitai_id'], data['civitai_version_id'],
                    data['preview_url'], data['description'], data['tags'],
                    data['mode'], data['noise_stage'], data['quality_score'],
                    data['download_count']
                ))
                inserted += 1
                print(f"  + 插入: {name}")

        except Exception as e:
            print(f"  ✗ 错误: {name} - {e}")
            skipped += 1

    # 提交
    conn.commit()

    print("\n" + "=" * 70)
    print("同步完成！")
    print(f"  插入: {inserted}")
    print(f"  更新: {updated}")
    print(f"  跳过: {skipped}")
    print(f"  总计: {len(loras)}")
    print("=" * 70)

    # 验证
    cursor.execute("SELECT COUNT(*) as count FROM lora_metadata")
    total = cursor.fetchone()[0]
    print(f"\n数据库中现有 {total} 个LORA")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    sync_loras_to_db()
