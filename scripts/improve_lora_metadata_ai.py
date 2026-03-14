#!/usr/bin/env python3
"""
AI驱动的LORA元数据改进工具
使用LLM自动为每个LORA生成同义词，无需预定义词库
"""
import pymysql
import json
import os
from typing import List, Dict

DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


def generate_synonyms_with_ai(name: str, description: str, tags: List[str]) -> Dict:
    """
    使用AI生成同义词和分类

    这里可以集成：
    1. OpenAI API
    2. Claude API
    3. 本地LLM（如Ollama）
    4. 其他LLM服务
    """

    # 示例：使用OpenAI API（需要配置API key）
    try:
        import openai

        openai.api_key = os.getenv('OPENAI_API_KEY')

        prompt = f"""
Given a LORA (LoRA model) with the following information:
- Name: {name}
- Description: {description}
- Current tags: {', '.join(tags)}

Please generate:
1. A list of 10-15 synonyms and related terms (in English)
2. A category (choose from: position, action, body, clothing, style, other)
3. A concise trigger prompt (50 words max)

Consider:
- Sexual positions and their variations
- Common slang and colloquial terms
- Different ways to describe the same concept
- Both formal and informal language

Return as JSON:
{{
  "synonyms": ["term1", "term2", ...],
  "category": "position",
  "trigger_prompt": "description here"
}}
"""

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert in adult content categorization and terminology."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )

        result = json.loads(response.choices[0].message.content)
        return result

    except ImportError:
        print("⚠️  OpenAI库未安装，使用本地规则")
        return generate_synonyms_local(name, description, tags)
    except Exception as e:
        print(f"⚠️  AI生成失败: {e}，使用本地规则")
        return generate_synonyms_local(name, description, tags)


def generate_synonyms_with_ollama(name: str, description: str, tags: List[str]) -> Dict:
    """
    使用本地Ollama生成同义词（免费，无需API key）
    """
    try:
        import requests

        prompt = f"""
Given this LORA information:
Name: {name}
Description: {description}
Tags: {', '.join(tags)}

Generate 10-15 synonyms and related terms. Return only a JSON object:
{{"synonyms": ["term1", "term2"], "category": "position", "trigger_prompt": "..."}}
"""

        response = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model': 'llama2',
                'prompt': prompt,
                'stream': False
            }
        )

        result = json.loads(response.json()['response'])
        return result

    except Exception as e:
        print(f"⚠️  Ollama生成失败: {e}，使用本地规则")
        return generate_synonyms_local(name, description, tags)


def generate_synonyms_local(name: str, description: str, tags: List[str]) -> Dict:
    """
    本地规则生成（fallback）
    """
    from improve_lora_metadata import generate_synonyms, get_lora_category

    synonyms = generate_synonyms(name, description, tags)
    category = get_lora_category(name, description, tags)
    trigger_prompt = ', '.join(synonyms[:5]) if synonyms else None

    return {
        'synonyms': synonyms,
        'category': category,
        'trigger_prompt': trigger_prompt
    }


def improve_lora_with_ai(lora_id: int, use_ai: str = 'local', dry_run: bool = True):
    """
    使用AI改进LORA元数据

    Args:
        lora_id: LORA ID
        use_ai: 'openai', 'ollama', 'local'
        dry_run: 是否只预览
    """
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT id, name, description, tags, trigger_prompt, category
        FROM lora_metadata
        WHERE id = %s
    """, (lora_id,))

    lora = cursor.fetchone()
    if not lora:
        print(f"❌ LORA #{lora_id} 不存在")
        return

    # 解析tags
    tags = lora.get('tags')
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except:
            tags = []
    if not tags:
        tags = []

    print(f"\n📝 LORA #{lora_id}: {lora['name']}")
    print(f"   使用AI: {use_ai}")

    # 生成改进建议
    if use_ai == 'openai':
        result = generate_synonyms_with_ai(lora['name'], lora['description'], tags)
    elif use_ai == 'ollama':
        result = generate_synonyms_with_ollama(lora['name'], lora['description'], tags)
    else:
        result = generate_synonyms_local(lora['name'], lora['description'], tags)

    # 合并标签
    new_tags = list(set(tags + result['synonyms']))
    new_trigger_prompt = result['trigger_prompt']
    new_category = result['category']

    print(f"\n✨ AI生成结果:")
    print(f"   Category: {new_category}")
    print(f"   新增同义词: {result['synonyms']}")
    print(f"   Trigger prompt: {new_trigger_prompt}")

    if not dry_run:
        cursor.execute("""
            UPDATE lora_metadata
            SET tags = %s,
                trigger_prompt = %s,
                category = %s
            WHERE id = %s
        """, (json.dumps(new_tags), new_trigger_prompt, new_category, lora_id))

        conn.commit()
        print(f"✅ 已更新 LORA #{lora_id}")
    else:
        print(f"🔍 Dry run模式，未实际更新")

    cursor.close()
    conn.close()


def improve_all_loras_with_ai(use_ai: str = 'local', dry_run: bool = True):
    """批量处理所有LORA"""
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("SELECT id FROM lora_metadata ORDER BY id")
    lora_ids = [row['id'] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    print(f"📊 找到 {len(lora_ids)} 个LORA")
    print(f"🤖 使用AI: {use_ai}")
    print(f"{'🔍 Dry run模式' if dry_run else '✍️ 实际更新模式'}\n")

    for i, lora_id in enumerate(lora_ids, 1):
        print(f"\n[{i}/{len(lora_ids)}]")
        improve_lora_with_ai(lora_id, use_ai=use_ai, dry_run=dry_run)

    print(f"\n{'=' * 60}")
    print(f"完成！处理了 {len(lora_ids)} 个LORA")


if __name__ == '__main__':
    import sys

    print("""
AI驱动的LORA元数据改进工具

支持的AI模式:
  --ai=local   : 使用本地规则（默认，无需配置）
  --ai=ollama  : 使用本地Ollama（需要安装Ollama）
  --ai=openai  : 使用OpenAI API（需要OPENAI_API_KEY环境变量）

用法:
  python scripts/improve_lora_metadata_ai.py                    # 本地规则，dry run
  python scripts/improve_lora_metadata_ai.py --apply            # 本地规则，实际更新
  python scripts/improve_lora_metadata_ai.py --ai=ollama        # 使用Ollama，dry run
  python scripts/improve_lora_metadata_ai.py --ai=openai --apply # 使用OpenAI，实际更新
  python scripts/improve_lora_metadata_ai.py 40 --ai=ollama    # 单个LORA，使用Ollama
""")

    use_ai = 'local'
    dry_run = True
    lora_id = None

    for arg in sys.argv[1:]:
        if arg.startswith('--ai='):
            use_ai = arg.split('=')[1]
        elif arg == '--apply':
            dry_run = False
        elif arg.isdigit():
            lora_id = int(arg)

    if lora_id:
        improve_lora_with_ai(lora_id, use_ai=use_ai, dry_run=dry_run)
    else:
        improve_all_loras_with_ai(use_ai=use_ai, dry_run=dry_run)
