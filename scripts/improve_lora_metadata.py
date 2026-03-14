#!/usr/bin/env python3
"""
改进LORA元数据工具
为LORA添加丰富的同义词和描述，提升搜索匹配质量
"""
import pymysql
import json

DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

# 常见姿势/动作的同义词映射
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

# 身体特征同义词
BODY_SYNONYMS = {
    'big_breasts': [
        'large breasts', 'huge breasts', 'big boobs', 'large boobs',
        'busty', 'big tits', 'huge tits', 'voluptuous',
        'big chest', 'large chest', 'saggy breasts', 'natural breasts',
        'massive breasts', 'enormous breasts', 'gigantic breasts',
        'heavy breasts', 'full breasts', 'bouncing breasts'
    ],
    'small_breasts': [
        'small boobs', 'petite breasts', 'small tits',
        'flat chest', 'small chest', 'tiny breasts',
        'perky breasts', 'cute breasts', 'modest breasts'
    ],
    'thick': [
        'thicc', 'curvy', 'voluptuous', 'full figured',
        'thick body', 'curvy body', 'plus size',
        'thick thighs', 'wide hips', 'big ass', 'fat ass',
        'bubble butt', 'round ass', 'juicy ass', 'pawg'
    ],
    'slim': [
        'thin', 'slender', 'skinny', 'petite',
        'slim body', 'lean', 'athletic', 'fit',
        'toned', 'tight body', 'small frame'
    ],
    'muscular': [
        'fit', 'athletic', 'toned', 'buff',
        'strong', 'ripped', 'defined', 'abs',
        'six pack', 'muscular body'
    ],
    'pregnant': [
        'preggo', 'expecting', 'knocked up', 'with child',
        'baby bump', 'pregnant belly', 'gravid'
    ],
    'lactating': [
        'milk', 'milking', 'breast milk', 'nursing',
        'lactation', 'milky breasts', 'leaking milk'
    ]
}

# 动作同义词
ACTION_SYNONYMS = {
    'blowjob': [
        'oral sex', 'fellatio', 'sucking', 'bj',
        'oral', 'cock sucking', 'dick sucking', 'giving head',
        'deepthroat', 'deep throat', 'throat fuck', 'face fuck',
        'sloppy blowjob', 'pov blowjob', 'kneeling blowjob',
        'sucking dick', 'sucking cock', 'oral pleasure'
    ],
    'handjob': [
        'hand job', 'stroking', 'jerking off', 'manual stimulation',
        'hand stimulation', 'tugging', 'jacking off',
        'pov handjob', 'two hands', 'double handjob',
        'stroking cock', 'stroking dick', 'hand fuck'
    ],
    'titfuck': [
        'paizuri', 'tit job', 'breast sex', 'boob job',
        'titty fuck', 'mammary intercourse', 'tit wank',
        'between breasts', 'breast fuck', 'boob fuck',
        'pov titfuck', 'titjob'
    ],
    'anal': [
        'anal sex', 'backdoor', 'butt sex', 'ass fuck',
        'anal intercourse', 'sodomy', 'anal penetration',
        'ass to mouth', 'atm', 'anal creampie',
        'rough anal', 'deep anal', 'anal pounding'
    ],
    'footjob': [
        'foot job', 'feet', 'foot fetish', 'foot worship',
        'toe job', 'foot sex', 'feet play', 'foot stimulation',
        'pov footjob', 'soles', 'toes'
    ],
    'rimjob': [
        'rim job', 'rimming', 'anilingus', 'ass licking',
        'eating ass', 'tongue in ass', 'ass worship',
        'rim', 'tossing salad'
    ],
    'cunnilingus': [
        'eating pussy', 'pussy licking', 'oral on woman',
        'going down', 'pussy eating', 'licking pussy',
        'tongue fuck', 'clit licking', 'oral pleasure'
    ],
    'fingering': [
        'finger fuck', 'digital penetration', 'finger play',
        'fingering pussy', 'fingering ass', 'finger bang',
        'finger insertion', 'manual stimulation'
    ],
    'squirting': [
        'squirt', 'female ejaculation', 'gushing',
        'wet orgasm', 'squirting orgasm', 'pussy squirt',
        'ejaculating', 'spraying'
    ],
    'creampie': [
        'cum inside', 'internal cumshot', 'breeding',
        'cum in pussy', 'cum in ass', 'filled',
        'insemination', 'impregnation', 'dripping cum'
    ],
    'facial': [
        'cum on face', 'face cumshot', 'cum facial',
        'face covered', 'bukakke', 'face blast',
        'cum shot face', 'facial cumshot'
    ],
    'cumshot': [
        'cum shot', 'ejaculation', 'cumming', 'orgasm',
        'cum', 'jizz', 'load', 'money shot',
        'cum blast', 'cum explosion', 'shooting cum'
    ],
    'double_penetration': [
        'dp', 'double pen', 'two cocks', 'double fuck',
        'double stuffed', 'both holes', 'dvp', 'dap',
        'double vaginal', 'double anal'
    ],
    'gangbang': [
        'gang bang', 'group sex', 'multiple partners',
        'train', 'running train', 'group fuck',
        'orgy', 'multiple men', 'gang fuck'
    ],
    'threesome': [
        '3some', 'three way', 'trio', 'threeway',
        'ffm', 'mmf', 'mff', 'group of three'
    ],
    'lesbian': [
        'girl on girl', 'lesbian sex', 'sapphic',
        'girl girl', 'lesbian love', 'tribbing',
        'scissoring', 'lesbian action'
    ],
    'masturbation': [
        'solo', 'self pleasure', 'touching herself',
        'touching himself', 'jilling off', 'jerking off',
        'rubbing', 'playing with herself', 'solo play',
        'self fuck', 'dildo play', 'toy play'
    ],
    'bondage': [
        'tied up', 'bound', 'rope', 'restraints',
        'bdsm', 'tied', 'ropes', 'bound and fucked',
        'restrained', 'tied and fucked', 'shibari'
    ],
    'spanking': [
        'ass slapping', 'butt slapping', 'spank',
        'slapping ass', 'red ass', 'punishment',
        'discipline', 'paddle', 'whipping'
    ],
    'choking': [
        'choke', 'throat grab', 'neck grab',
        'breath play', 'strangling', 'hand on throat',
        'choking sex', 'rough choking'
    ]
}

# 场景/情境同义词
SCENARIO_SYNONYMS = {
    'pov': [
        'point of view', 'first person', 'pov shot',
        'pov angle', 'your pov', 'viewer pov',
        'immersive', 'first person view'
    ],
    'public': [
        'public sex', 'outdoor', 'outside', 'in public',
        'exhibitionism', 'public place', 'risky sex',
        'caught', 'outdoor sex'
    ],
    'shower': [
        'bathroom', 'wet', 'shower sex', 'bath',
        'bathtub', 'wet sex', 'bathroom sex',
        'steamy', 'water'
    ],
    'office': [
        'workplace', 'desk', 'secretary', 'boss',
        'office sex', 'work sex', 'professional',
        'business', 'coworker'
    ],
    'school': [
        'classroom', 'teacher', 'student', 'uniform',
        'schoolgirl', 'college', 'campus', 'education'
    ],
    'gym': [
        'workout', 'fitness', 'gym sex', 'exercise',
        'athletic', 'sports', 'locker room', 'trainer'
    ],
    'massage': [
        'massage sex', 'oil', 'sensual massage', 'erotic massage',
        'massage table', 'rubbing', 'body massage', 'happy ending'
    ],
    'sleep': [
        'sleeping', 'sleep sex', 'unconscious', 'passed out',
        'sleep fuck', 'sleeping beauty', 'somnophilia'
    ]
}


# 构建反向索引：同义词 → KEY
def build_reverse_index():
    """构建从同义词到KEY的反向映射"""
    reverse_index = {}

    # 处理所有同义词字典
    all_synonyms = {
        **POSITION_SYNONYMS,
        **BODY_SYNONYMS,
        **ACTION_SYNONYMS,
        **SCENARIO_SYNONYMS
    }

    for key, synonyms in all_synonyms.items():
        # KEY本身也映射到自己
        reverse_index[key.lower()] = key
        # 每个同义词都映射到KEY
        for syn in synonyms:
            reverse_index[syn.lower()] = key

    return reverse_index


# 全局反向索引
SYNONYM_TO_KEY = build_reverse_index()


def get_lora_category(name: str, description: str, tags: list) -> str:
    """根据LORA名称和描述推断类别"""
    name_lower = name.lower()
    desc_lower = (description or '').lower()
    all_text = f"{name_lower} {desc_lower} {' '.join(tags)}"

    # 姿势类
    for pos in POSITION_SYNONYMS.keys():
        if pos in all_text:
            return 'position'

    # 动作类
    for action in ACTION_SYNONYMS.keys():
        if action in all_text:
            return 'action'

    # 身体特征类
    for body in BODY_SYNONYMS.keys():
        if body in all_text:
            return 'body'

    # 场景类
    for scenario in SCENARIO_SYNONYMS.keys():
        if scenario in all_text:
            return 'scenario'

    return 'other'


def generate_synonyms(name: str, description: str, tags: list) -> list:
    """为LORA生成同义词列表（支持双向映射）"""
    matched_keys = set()
    name_lower = name.lower()

    # 从名称中查找匹配的KEY
    for word in name_lower.replace('_', ' ').split():
        if word in SYNONYM_TO_KEY:
            matched_keys.add(SYNONYM_TO_KEY[word])

    # 从标签中查找匹配的KEY
    for tag in tags:
        tag_lower = tag.lower()
        # 检查完整标签
        if tag_lower in SYNONYM_TO_KEY:
            matched_keys.add(SYNONYM_TO_KEY[tag_lower])
        # 检查标签中的每个词
        for word in tag_lower.replace('_', ' ').split():
            if word in SYNONYM_TO_KEY:
                matched_keys.add(SYNONYM_TO_KEY[word])

    # 收集所有匹配KEY的同义词
    synonyms = []
    all_synonym_dicts = [POSITION_SYNONYMS, BODY_SYNONYMS, ACTION_SYNONYMS, SCENARIO_SYNONYMS]

    for key in matched_keys:
        for syn_dict in all_synonym_dicts:
            if key in syn_dict:
                synonyms.extend(syn_dict[key])

    # 去重
    return list(set(synonyms))


def improve_lora_metadata(lora_id: int, dry_run: bool = True):
    """改进单个LORA的元数据"""
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 获取当前LORA信息
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
    print(f"   当前描述: {lora['description'][:100] if lora['description'] else 'None'}...")
    print(f"   当前标签: {tags}")
    print(f"   当前trigger_prompt: {lora['trigger_prompt']}")
    print(f"   当前category: {lora['category']}")

    # 生成改进建议
    synonyms = generate_synonyms(lora['name'], lora['description'], tags)
    suggested_category = get_lora_category(lora['name'], lora['description'], tags)

    # 合并新标签（保留原有标签，添加同义词）
    new_tags = list(set(tags + synonyms))

    # 生成trigger_prompt（如果没有）
    new_trigger_prompt = lora['trigger_prompt']
    if not new_trigger_prompt and synonyms:
        new_trigger_prompt = ', '.join(synonyms[:5])  # 取前5个同义词

    print(f"\n✨ 改进建议:")
    print(f"   建议category: {suggested_category}")
    print(f"   新增同义词: {synonyms}")
    print(f"   新标签列表: {new_tags}")
    print(f"   建议trigger_prompt: {new_trigger_prompt}")

    if not dry_run:
        # 更新数据库
        cursor.execute("""
            UPDATE lora_metadata
            SET tags = %s,
                trigger_prompt = %s,
                category = %s
            WHERE id = %s
        """, (json.dumps(new_tags), new_trigger_prompt, suggested_category, lora_id))

        conn.commit()
        print(f"✅ 已更新 LORA #{lora_id}")
    else:
        print(f"🔍 Dry run模式，未实际更新")

    cursor.close()
    conn.close()


def improve_all_loras(dry_run: bool = True):
    """改进所有LORA的元数据"""
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("SELECT id FROM lora_metadata ORDER BY id")
    lora_ids = [row['id'] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    print(f"📊 找到 {len(lora_ids)} 个LORA")
    print(f"{'🔍 Dry run模式' if dry_run else '✍️ 实际更新模式'}\n")

    for lora_id in lora_ids:
        improve_lora_metadata(lora_id, dry_run=dry_run)

    print(f"\n{'=' * 60}")
    print(f"完成！处理了 {len(lora_ids)} 个LORA")
    if dry_run:
        print("这是dry run，没有实际修改数据库")
        print("运行 python scripts/improve_lora_metadata.py --apply 来实际应用更改")


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == '--apply':
            print("⚠️  将实际修改数据库！")
            confirm = input("确认继续？(yes/no): ")
            if confirm.lower() == 'yes':
                improve_all_loras(dry_run=False)
            else:
                print("已取消")
        elif sys.argv[1].isdigit():
            lora_id = int(sys.argv[1])
            dry_run = '--apply' not in sys.argv
            improve_lora_metadata(lora_id, dry_run=dry_run)
        else:
            print("用法:")
            print("  python scripts/improve_lora_metadata.py              # Dry run所有LORA")
            print("  python scripts/improve_lora_metadata.py --apply     # 实际更新所有LORA")
            print("  python scripts/improve_lora_metadata.py 40          # Dry run单个LORA")
            print("  python scripts/improve_lora_metadata.py 40 --apply # 实际更新单个LORA")
    else:
        # 默认dry run所有LORA
        improve_all_loras(dry_run=True)
