#!/usr/bin/env python3
"""
提取资源数据并导入到 tudou_soga 数据库
从 3 个数据源提取：图片、视频、生成视频
"""
import pymysql
import json
from datetime import datetime

# 源数据库配置
SOURCE_DB = {
    'host': 'use-cdb-bx16mn14.sql.tencentcdb.com',
    'port': 24426,
    'user': 'root',
    'password': 'mainr2@sdaXlsda',
    'charset': 'utf8mb4'
}

# 目标数据库配置
TARGET_DB = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

def extract_images(source_conn, limit=None):
    """提取图片资源"""
    print(f"[{datetime.now()}] 开始提取图片资源...")
    cursor = source_conn.cursor(pymysql.cursors.DictCursor)

    sql = """
    SELECT
        m.uuid as source_id,
        JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.url')) as url,
        JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.desc')) as prompt,
        r.unlocked,
        m.create_at
    FROM tudou_im.private_message m
    JOIN tudou_im.msg_res r ON m.uuid = r.msg_uuid
    WHERE m.msg_type = 2
        AND r.int_msg_type = 2
        AND r.unlocked = 1
        AND JSON_EXTRACT(m.content, '$.desc') IS NOT NULL
        AND JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.desc')) != ''
        AND JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.desc')) != '**'
    """
    if limit:
        sql += f" LIMIT {limit}"

    cursor.execute(sql)
    results = cursor.fetchall()
    print(f"[{datetime.now()}] 提取到 {len(results)} 条图片记录")

    return [
        ('image', 'private_message', r['source_id'], r['url'], r['prompt'], r['unlocked'])
        for r in results
    ]

def extract_videos(source_conn, limit=None):
    """提取视频资源"""
    print(f"[{datetime.now()}] 开始提取视频资源...")
    cursor = source_conn.cursor(pymysql.cursors.DictCursor)

    sql = """
    SELECT
        m.uuid as source_id,
        JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.url')) as url,
        JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.desc')) as prompt,
        r.unlocked,
        m.create_at
    FROM tudou_im.private_message m
    JOIN tudou_im.msg_res r ON m.uuid = r.msg_uuid
    WHERE m.msg_type = 4
        AND r.int_msg_type = 4
        AND r.unlocked = 1
        AND JSON_EXTRACT(m.content, '$.desc') IS NOT NULL
        AND JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.desc')) != ''
        AND JSON_UNQUOTE(JSON_EXTRACT(m.content, '$.desc')) != '**'
    """
    if limit:
        sql += f" LIMIT {limit}"

    cursor.execute(sql)
    results = cursor.fetchall()
    print(f"[{datetime.now()}] 提取到 {len(results)} 条视频记录")

    return [
        ('video', 'private_message', r['source_id'], r['url'], r['prompt'], r['unlocked'])
        for r in results
    ]

def extract_generated_videos(source_conn, limit=None):
    """提取生成视频资源"""
    print(f"[{datetime.now()}] 开始提取生成视频资源...")
    cursor = source_conn.cursor(pymysql.cursors.DictCursor)

    sql = """
    SELECT
        id as source_id,
        output_url as url,
        JSON_UNQUOTE(JSON_EXTRACT(request, '$.prompt')) as prompt,
        created_at
    FROM tudou_ai.tb_generate_video_record
    WHERE state = 2
        AND JSON_EXTRACT(request, '$.prompt') IS NOT NULL
        AND JSON_UNQUOTE(JSON_EXTRACT(request, '$.prompt')) != ''
    """
    if limit:
        sql += f" LIMIT {limit}"

    cursor.execute(sql)
    results = cursor.fetchall()
    print(f"[{datetime.now()}] 提取到 {len(results)} 条生成视频记录")

    return [
        ('generated_video', 'tb_generate_video_record', str(r['source_id']), r['url'], r['prompt'], 1)
        for r in results
    ]

def insert_resources(target_conn, resources, batch_size=1000):
    """批量插入资源"""
    print(f"[{datetime.now()}] 开始插入 {len(resources)} 条资源...")
    cursor = target_conn.cursor()

    sql = """
    INSERT INTO resources (resource_type, source_table, source_id, url, prompt, unlocked)
    VALUES (%s, %s, %s, %s, %s, %s)
    """

    inserted = 0
    for i in range(0, len(resources), batch_size):
        batch = resources[i:i+batch_size]
        cursor.executemany(sql, batch)
        target_conn.commit()
        inserted += len(batch)
        print(f"[{datetime.now()}] 已插入 {inserted}/{len(resources)} 条")

    print(f"[{datetime.now()}] 插入完成")

def main():
    # 连接数据库
    print(f"[{datetime.now()}] 连接源数据库...")
    source_conn = pymysql.connect(**SOURCE_DB)

    print(f"[{datetime.now()}] 连接目标数据库...")
    target_conn = pymysql.connect(**TARGET_DB)

    try:
        # 提取数据（先提取少量测试）
        all_resources = []

        # 图片（先提取 1000 条测试）
        images = extract_images(source_conn, limit=1000)
        all_resources.extend(images)

        # 视频（先提取 1000 条测试）
        videos = extract_videos(source_conn, limit=1000)
        all_resources.extend(videos)

        # 生成视频（全部提取）
        generated = extract_generated_videos(source_conn, limit=None)
        all_resources.extend(generated)

        # 插入数据
        if all_resources:
            insert_resources(target_conn, all_resources)

        # 统计
        cursor = target_conn.cursor()
        cursor.execute("SELECT resource_type, COUNT(*) as count FROM resources GROUP BY resource_type")
        stats = cursor.fetchall()
        print(f"\n[{datetime.now()}] 导入统计:")
        for row in stats:
            print(f"  {row[0]}: {row[1]} 条")

    finally:
        source_conn.close()
        target_conn.close()
        print(f"\n[{datetime.now()}] 完成")

if __name__ == '__main__':
    main()
