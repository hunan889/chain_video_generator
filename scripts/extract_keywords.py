#!/usr/bin/env python3
"""
从 prompt 提取关键词并打标签
调用本地 Qwen3-14B (端口 20001) 进行关键词提取
"""
import pymysql
import requests
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

# Qwen3-14B API 配置
LLM_API = "http://localhost:20001/v1/chat/completions"

# 系统 Prompt
SYSTEM_PROMPT = """You are an expert at analyzing adult content prompts and extracting relevant tags.

Extract tags from the given prompt in these categories:
- position: sexual positions (e.g., cowgirl, missionary, doggy, oral, anal)
- body_parts: visible body parts (e.g., breasts, ass, pussy, cock, face)
- action: actions (e.g., riding, penetration, blowjob, handjob, cumshot, facial)
- modifier: modifiers (e.g., pov, rough, gentle, passionate, public)
- scene: scene/location (e.g., bedroom, bathroom, outdoor, office, car)
- clothing: clothing state (e.g., naked, lingerie, stockings, uniform)
- expression: facial expressions (e.g., ahegao, ecstasy, pleasure, seductive)

IMPORTANT: Return ONLY a valid JSON object, no explanations, no markdown, no thinking process.
Example: {"position": ["cowgirl"], "body_parts": ["breasts", "ass"], "action": ["riding"], "modifier": ["pov"], "scene": ["bedroom"], "clothing": ["naked"], "expression": ["ecstasy"]}

If a category has no relevant tags, use an empty array."""

def call_llm(prompt):
    """调用 Qwen3-14B 提取关键词"""
    try:
        response = requests.post(LLM_API, json={
            "model": "/home/gime/soft/Qwen3-14B-v2-Abliterated",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract tags from this prompt:\n\n{prompt}"}
            ],
            "temperature": 0.3,
            "max_tokens": 500
        }, timeout=30)

        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']

            # 移除 <think> 标签内容
            import re
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
            content = content.strip()

            # 尝试解析 JSON
            try:
                # 提取 JSON 部分（可能包含在 markdown 代码块中）
                if '```json' in content:
                    content = content.split('```json')[1].split('```')[0].strip()
                elif '```' in content:
                    content = content.split('```')[1].split('```')[0].strip()

                # 查找 JSON 对象（从第一个 { 到最后一个 }）
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    content = json_match.group(0)

                tags_dict = json.loads(content)
                return tags_dict
            except json.JSONDecodeError as e:
                print(f"JSON 解析失败: {content[:200]}")
                return None
        else:
            print(f"LLM API 错误: {response.status_code}")
            return None
    except Exception as e:
        print(f"调用 LLM 失败: {e}")
        return None

def get_or_create_tag(cursor, tag_name, category=None):
    """获取或创建标签"""
    # 查找标签
    cursor.execute("SELECT id FROM tags WHERE name = %s", (tag_name,))
    result = cursor.fetchone()
    if result:
        return result[0]

    # 创建新标签
    cursor.execute(
        "INSERT INTO tags (name, category, usage_count) VALUES (%s, %s, 0)",
        (tag_name, category)
    )
    return cursor.lastrowid

def process_resource(resource_id, prompt, conn):
    """处理单个资源"""
    try:
        # 调用 LLM 提取关键词
        tags_dict = call_llm(prompt)
        if not tags_dict:
            return False, "LLM 提取失败"

        cursor = conn.cursor()

        # 插入标签
        tag_count = 0
        for category, tags in tags_dict.items():
            if not isinstance(tags, list):
                continue
            for tag_name in tags:
                if not tag_name or not isinstance(tag_name, str):
                    continue
                tag_name = tag_name.lower().strip()
                if not tag_name:
                    continue

                # 获取或创建标签
                tag_id = get_or_create_tag(cursor, tag_name, category)

                # 关联资源和标签
                cursor.execute("""
                    INSERT IGNORE INTO resource_tags (resource_id, tag_id, source, confidence)
                    VALUES (%s, %s, 'auto', 1.0)
                """, (resource_id, tag_id))

                # 更新标签使用次数
                cursor.execute("UPDATE tags SET usage_count = usage_count + 1 WHERE id = %s", (tag_id,))

                tag_count += 1

        conn.commit()
        return True, f"提取 {tag_count} 个标签"

    except Exception as e:
        return False, str(e)

def main():
    print(f"[{datetime.now()}] 开始关键词提取任务...")

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 获取待处理的资源（全量处理）
    cursor.execute("""
        SELECT r.id, r.prompt
        FROM resources r
        LEFT JOIN extraction_tasks t ON r.id = t.resource_id
        WHERE t.id IS NULL OR t.status = 'failed'
        ORDER BY r.id
    """)
    resources = cursor.fetchall()

    print(f"[{datetime.now()}] 找到 {len(resources)} 条待处理资源")

    if not resources:
        print("没有待处理资源")
        conn.close()
        return

    # 创建提取任务
    for r in resources:
        cursor.execute("""
            INSERT INTO extraction_tasks (resource_id, status)
            VALUES (%s, 'pending')
            ON DUPLICATE KEY UPDATE status = 'pending', retry_count = retry_count + 1
        """, (r['id'],))
    conn.commit()
    conn.close()

    # 并发处理资源
    success_count = 0
    failed_count = 0

    def process_one(resource_data):
        """处理单个资源（在独立连接中）"""
        resource_id, prompt = resource_data
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()

        try:
            # 更新状态为 processing
            cursor.execute("""
                UPDATE extraction_tasks SET status = 'processing'
                WHERE resource_id = %s
            """, (resource_id,))
            conn.commit()

            # 处理
            success, message = process_resource(resource_id, prompt, conn)

            if success:
                cursor.execute("""
                    UPDATE extraction_tasks SET status = 'completed'
                    WHERE resource_id = %s
                """, (resource_id,))
                conn.commit()
                return True, resource_id, message
            else:
                cursor.execute("""
                    UPDATE extraction_tasks SET status = 'failed', error_message = %s
                    WHERE resource_id = %s
                """, (message, resource_id))
                conn.commit()
                return False, resource_id, message
        except Exception as e:
            cursor.execute("""
                UPDATE extraction_tasks SET status = 'failed', error_message = %s
                WHERE resource_id = %s
            """, (str(e), resource_id))
            conn.commit()
            return False, resource_id, str(e)
        finally:
            conn.close()

    # 使用线程池并发处理
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(process_one, (r['id'], r['prompt'])): r['id']
            for r in resources
        }

        for i, future in enumerate(as_completed(futures), 1):
            try:
                success, resource_id, message = future.result()
                if success:
                    success_count += 1
                    print(f"[{datetime.now()}] [{i}/{len(resources)}] ✓ 资源 {resource_id}: {message}")
                else:
                    failed_count += 1
                    print(f"[{datetime.now()}] [{i}/{len(resources)}] ✗ 资源 {resource_id}: {message[:100]}")
            except Exception as e:
                failed_count += 1
                print(f"[{datetime.now()}] [{i}/{len(resources)}] ✗ 异常: {e}")

            # 每 100 条输出进度
            if i % 100 == 0:
                print(f"\n[{datetime.now()}] 进度: {i}/{len(resources)}, 成功: {success_count}, 失败: {failed_count}\n")

    print(f"\n[{datetime.now()}] 完成")
    print(f"成功: {success_count}, 失败: {failed_count}")

if __name__ == '__main__':
    main()
