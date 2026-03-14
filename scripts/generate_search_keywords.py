#!/usr/bin/env python3
"""
自动生成搜索关键词工具
为LORA和图片生成 search_keywords 字段
"""
import sys
sys.path.insert(0, '/home/gime/soft/wan22-service')

import pymysql
import json
import argparse

DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

# 导入同义词库（从 improve_lora_metadata.py）
POSITION_SYNONYMS = {
    'doggy': [
        'doggy style', 'from behind', 'rear entry', 'on all fours',
        'doggystyle', 'doggy position', 'back entry', 'prone position',
        'rear view', 'back view', 'behind view', 'ass view'
    ],
    'cowgirl': [
        'woman on top', 'riding', 'girl on top', 'reverse cowgirl',
        'cowgirl position', 'riding position', 'woman riding',
        'girl riding', 'riding cock', 'riding dick', 'bouncing',
        'grinding', 'woman dominant', 'active woman'
    ],
    'missionary': [
        'face to face', 'man on top', 'missionary position',
        'frontal position', 'classic position', 'vanilla position',
        'legs spread', 'legs up', 'legs open', 'intimate position'
    ],
    'face_down_ass_up': [
        'face down', 'ass up', 'prone', 'face down position',
        'downward dog', 'submissive position', 'prone bone',
        'ass in the air', 'head down ass up'
    ],
    'standing': [
        'standing sex', 'standing position', 'vertical',
        'stand and carry', 'standing fuck', 'standing doggy',
        'wall sex', 'against wall', 'vertical sex'
    ],
    'spooning': [
        'spoon', 'side by side', 'spooning position',
        'side position', 'lying side', 'side fuck',
        'sideways', 'cuddle fuck'
    ],
    'suspended': [
        'suspended congress', 'lifted', 'carry position',
        'standing carry', 'suspended position', 'lifted fuck',
        'carry and fuck', 'suspended sex'
    ],
    '69': [
        'sixty nine', 'mutual oral', 'double oral',
        '69 position', 'simultaneous oral'
    ],
    'lotus': [
        'lotus position', 'sitting face to face', 'intimate sitting',
        'yab yum', 'seated position'
    ],
    'piledriver': [
        'pile driver', 'piledriver position', 'vertical missionary',
        'shoulders down', 'legs over head'
    ],
    'mating_press': [
        'mating press', 'breeding press', 'deep missionary',
        'legs pinned', 'full nelson', 'pressed down'
    ]
}

BODY_SYNONYMS = {
    'big_breasts': [
        'large breasts', 'huge breasts', 'big boobs', 'large boobs',
        'busty', 'big tits', 'huge tits', 'voluptuous'
    ],
    'small_breasts': [
        'small boobs', 'petite breasts', 'small tits',
        'flat chest', 'small chest', 'tiny breasts'
    ],
    'thick': [
        'thicc', 'curvy', 'voluptuous', 'full figured',
        'thick body', 'curvy body', 'thick thighs', 'wide hips'
    ],
    'slim': [
        'thin', 'slender', 'skinny', 'petite',
        'slim body', 'lean', 'athletic', 'fit'
    ],
    'muscular': [
        'fit', 'athletic', 'toned', 'buff',
        'ripped', 'abs', 'six pack'
    ],
    'pregnant': [
        'preggo', 'expecting', 'knocked up',
        'baby bump', 'pregnant belly'
    ],
    'lactating': [
        'milk', 'milking', 'breast milk',
        'nursing', 'lactation'
    ]
}

ACTION_SYNONYMS = {
    'blowjob': [
        'bj', 'oral', 'sucking', 'cock sucking',
        'dick sucking', 'fellatio', 'giving head', 'deepthroat',
        'face fuck', 'throat fuck', 'pov blowjob'
    ],
    'handjob': [
        'hj', 'hand job', 'jerking off', 'stroking',
        'handy', 'manual stimulation', 'pov handjob'
    ],
    'titfuck': [
        'tit fuck', 'paizuri', 'breast fuck', 'boob job',
        'tit job', 'between breasts', 'tit wank'
    ],
    'anal': [
        'anal sex', 'ass fuck', 'butt fuck', 'backdoor',
        'anal penetration', 'ass to mouth', 'rough anal'
    ],
    'footjob': [
        'foot job', 'feet', 'foot fetish',
        'foot worship', 'toe job'
    ],
    'rimjob': [
        'rimming', 'anilingus', 'ass licking',
        'eating ass', 'rim job'
    ],
    'cunnilingus': [
        'eating pussy', 'pussy licking', 'going down',
        'oral sex', 'licking', 'pussy eating'
    ],
    'fingering': [
        'finger fuck', 'digital penetration', 'finger play',
        'fingering pussy', 'finger bang'
    ],
    'squirting': [
        'squirt', 'female ejaculation', 'gushing',
        'squirting orgasm', 'wet orgasm'
    ],
    'creampie': [
        'cum inside', 'internal cumshot', 'breeding',
        'cream pie', 'filled pussy', 'dripping cum'
    ],
    'facial': [
        'cum on face', 'face cumshot', 'bukakke',
        'facial cumshot', 'face blast', 'cum facial'
    ],
    'cumshot': [
        'ejaculation', 'cumming', 'money shot',
        'cum shot', 'jizz', 'load'
    ],
    'double_penetration': [
        'dp', 'double pen', 'dvp', 'dap',
        'double penetration', 'two holes'
    ],
    'gangbang': [
        'gang bang', 'group sex', 'orgy',
        'multiple partners', 'train'
    ],
    'threesome': [
        '3some', 'three way', 'ffm', 'mmf',
        'threeway', 'trio'
    ],
    'lesbian': [
        'girl on girl', 'sapphic', 'tribbing',
        'scissoring', 'lesbian sex'
    ],
    'masturbation': [
        'solo', 'self pleasure', 'dildo play',
        'toy play', 'masturbating', 'touching herself'
    ],
    'bondage': [
        'tied up', 'bound', 'bdsm', 'shibari',
        'rope', 'restraints', 'tied'
    ],
    'spanking': [
        'ass slapping', 'punishment', 'discipline',
        'spank', 'slapping ass'
    ],
    'choking': [
        'choke', 'throat grab', 'breath play',
        'choking sex', 'hand on throat'
    ]
}

SCENARIO_SYNONYMS = {
    'pov': [
        'point of view', 'first person', 'immersive',
        'pov sex', 'first person view'
    ],
    'public': [
        'public sex', 'outdoor', 'exhibitionism',
        'outside', 'public place'
    ],
    'shower': [
        'bathroom', 'wet', 'shower sex',
        'bath', 'bathtub'
    ],
    'office': [
        'workplace', 'desk', 'secretary',
        'boss', 'office sex'
    ],
    'school': [
        'classroom', 'teacher', 'student',
        'uniform', 'schoolgirl'
    ],
    'gym': [
        'workout', 'fitness', 'locker room',
        'trainer', 'gym sex'
    ],
    'massage': [
        'massage sex', 'oil', 'sensual massage',
        'massage table', 'erotic massage'
    ],
    'sleep': [
        'sleeping', 'sleep sex', 'somnophilia',
        'sleeping girl', 'unconscious'
    ]
}

# 构建反向索引
def build_reverse_index():
    """构建从同义词到KEY的反向映射"""
    reverse_index = {}
    all_synonyms = {
        **POSITION_SYNONYMS,
        **BODY_SYNONYMS,
        **ACTION_SYNONYMS,
        **SCENARIO_SYNONYMS
    }

    for key, synonyms in all_synonyms.items():
        # KEY本身也映射到自己
        reverse_index[key.lower()] = key
        # 所有同义词映射到KEY
        for syn in synonyms:
            reverse_index[syn.lower()] = key

    return reverse_index

SYNONYM_TO_KEY = build_reverse_index()


def generate_lora_search_keywords(lora_name: str, tags: list, description: str = "") -> str:
    """自动生成LORA搜索关键词"""
    keywords = set()

    # 1. 从LORA名称中提取关键词
    name_words = lora_name.lower().replace('_', ' ').split()
    for word in name_words:
        if word in SYNONYM_TO_KEY:
            key = SYNONYM_TO_KEY[word]
            # 添加KEY本身
            keywords.add(key)
            # 添加最常用的同义词
            if key in POSITION_SYNONYMS:
                keywords.update(POSITION_SYNONYMS[key][:7])
            elif key in ACTION_SYNONYMS:
                keywords.update(ACTION_SYNONYMS[key][:7])
            elif key in BODY_SYNONYMS:
                keywords.update(BODY_SYNONYMS[key][:5])
            elif key in SCENARIO_SYNONYMS:
                keywords.update(SCENARIO_SYNONYMS[key][:5])

    # 2. 从tags中提取关键词
    if tags:
        for tag in tags[:5]:
            tag_lower = tag.lower()
            if tag_lower in SYNONYM_TO_KEY:
                key = SYNONYM_TO_KEY[tag_lower]
                keywords.add(key)
                # 添加部分同义词
                if key in POSITION_SYNONYMS:
                    keywords.update(POSITION_SYNONYMS[key][:5])
                elif key in ACTION_SYNONYMS:
                    keywords.update(ACTION_SYNONYMS[key][:5])

    # 3. 限制数量并格式化
    keywords_list = list(keywords)[:15]

    return ', '.join(keywords_list)


def generate_image_search_keywords(url: str, prompt: str = "", tags: list = []) -> str:
    """自动生成图片搜索关键词"""
    # 如果prompt较短且描述性强，直接使用
    if prompt and len(prompt) < 150 and len(prompt) > 20:
        return prompt

    keywords = []

    # 1. 从tags中提取
    if tags:
        keywords.extend(tags[:5])

    # 2. 从prompt中提取关键词
    if prompt:
        words = prompt.lower().replace(',', ' ').split()
        stop_words = {'a', 'an', 'the', 'is', 'are', 'with', 'at', 'in', 'on', 'of', 'and', 'or'}
        keywords.extend([w for w in words if w not in stop_words and len(w) > 3][:10])

    # 3. 从URL中提取文件名
    if not keywords and url:
        # 从URL提取文件名
        filename = url.split('/')[-1].split('?')[0]
        name_parts = filename.replace('_', ' ').replace('-', ' ').replace('.', ' ').split()
        keywords.extend([p for p in name_parts if len(p) > 3][:5])

    # 4. 去重并限制数量
    keywords = list(dict.fromkeys(keywords))[:15]

    return ', '.join(keywords)


def process_loras(conn, dry_run=True):
    """处理所有LORA"""
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 查询所有LORA
    cursor.execute("""
        SELECT id, name, description, tags, search_keywords
        FROM lora_metadata
        ORDER BY id
    """)
    loras = cursor.fetchall()

    print(f"\n{'='*70}")
    print(f"处理LORA: 共 {len(loras)} 个")
    print(f"{'='*70}\n")

    updated_count = 0
    skipped_count = 0

    for lora in loras:
        lora_id = lora['id']
        lora_name = lora['name']
        description = lora.get('description', '')
        existing_keywords = lora.get('search_keywords')

        # 如果已有 search_keywords，跳过
        if existing_keywords:
            print(f"[{lora_id}] {lora_name}: 已有关键词，跳过")
            skipped_count += 1
            continue

        # 解析tags
        tags = []
        if lora.get('tags'):
            try:
                tags = json.loads(lora['tags']) if isinstance(lora['tags'], str) else lora['tags']
            except:
                tags = []

        # 生成关键词
        search_keywords = generate_lora_search_keywords(lora_name, tags, description)

        print(f"[{lora_id}] {lora_name}")
        print(f"  生成关键词: {search_keywords}")

        if not dry_run:
            # 更新数据库
            cursor.execute("""
                UPDATE lora_metadata
                SET search_keywords = %s
                WHERE id = %s
            """, (search_keywords, lora_id))
            updated_count += 1
            print(f"  ✓ 已更新")
        else:
            print(f"  (预览模式，未更新)")

        print()

    if not dry_run:
        conn.commit()

    cursor.close()

    print(f"\n{'='*70}")
    print(f"LORA处理完成")
    print(f"{'='*70}")
    print(f"  更新: {updated_count}")
    print(f"  跳过: {skipped_count}")
    print(f"{'='*70}\n")


def process_images(conn, dry_run=True):
    """处理所有图片"""
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 查询所有图片
    cursor.execute("""
        SELECT id, url, prompt, search_keywords
        FROM resources
        WHERE resource_type = 'image'
        ORDER BY id
    """)
    images = cursor.fetchall()

    print(f"\n{'='*70}")
    print(f"处理图片: 共 {len(images)} 个")
    print(f"{'='*70}\n")

    updated_count = 0
    skipped_count = 0

    for image in images:
        image_id = image['id']
        url = image['url']
        prompt = image.get('prompt', '')
        existing_keywords = image.get('search_keywords')

        # 如果已有 search_keywords，跳过
        if existing_keywords:
            print(f"[{image_id}] {url[:50]}...: 已有关键词，跳过")
            skipped_count += 1
            continue

        # 生成关键词（不使用tags，因为resources表没有tags字段）
        search_keywords = generate_image_search_keywords(url, prompt, [])

        print(f"[{image_id}] {url[:50]}...")
        print(f"  生成关键词: {search_keywords[:100]}...")

        if not dry_run:
            # 更新数据库
            cursor.execute("""
                UPDATE resources
                SET search_keywords = %s
                WHERE id = %s
            """, (search_keywords, image_id))
            updated_count += 1
            print(f"  ✓ 已更新")
        else:
            print(f"  (预览模式，未更新)")

        print()

    if not dry_run:
        conn.commit()

    cursor.close()

    print(f"\n{'='*70}")
    print(f"图片处理完成")
    print(f"{'='*70}")
    print(f"  更新: {updated_count}")
    print(f"  跳过: {skipped_count}")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description='自动生成搜索关键词')
    parser.add_argument('--apply', action='store_true', help='实际应用到数据库（默认为预览模式）')
    parser.add_argument('--lora-only', action='store_true', help='只处理LORA')
    parser.add_argument('--image-only', action='store_true', help='只处理图片')

    args = parser.parse_args()

    dry_run = not args.apply

    if dry_run:
        print("\n" + "="*70)
        print("预览模式 (Dry Run)")
        print("="*70)
        print("使用 --apply 参数来实际应用到数据库")
        print("="*70 + "\n")
    else:
        print("\n" + "="*70)
        print("应用模式 (Apply)")
        print("="*70)
        print("将实际更新数据库")
        print("="*70 + "\n")

    # 连接数据库
    conn = pymysql.connect(**DB_CONFIG)

    try:
        if args.image_only:
            process_images(conn, dry_run)
        elif args.lora_only:
            process_loras(conn, dry_run)
        else:
            # 处理LORA和图片
            process_loras(conn, dry_run)
            process_images(conn, dry_run)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
