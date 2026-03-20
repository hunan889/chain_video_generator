"""
姿势同义词词典
"""

# 姿势同义词映射
POSE_SYNONYMS = {
    "cowgirl": [
        "woman on top",
        "girl on top",
        "riding",
        "woman riding",
        "riding position",
        "女上位"
    ],
    "reverse_cowgirl": [
        "reverse woman on top",
        "reverse riding",
        "backwards riding",
        "woman backwards",
        "backwards on top",
        "reverse girl on top",
        "riding backwards",
        "from backwards",
        "反向女上位"
    ],
    "missionary": [
        "man on top",
        "face to face",
        "classic position",
        "lying on bed",
        "lying down",
        "spread legs",
        "legs spread",
        "on her back",
        "传教士",
        "正常位"
    ],
    "doggy": [
        "from behind",
        "rear entry",
        "on all fours",
        "on her fours",
        "hands and knees",
        "doggy style",
        "后入",
        "后入式"
    ],
    "blowjob": [
        "oral sex",
        "fellatio",
        "oral",
        "bj",
        "口交"
    ],
    "69": [
        "sixty nine",
        "mutual oral",
        "69式"
    ],
    "standing": [
        "standing sex",
        "standing position",
        "vertical",
        "站立式"
    ],
    "spooning": [
        "side by side",
        "lying side",
        "侧卧式"
    ],
    "footjob": [
        "foot job",
        "feet job",
        "using feet",
        "using foot",
        "using her feet",
        "using his feet",
        "with her feet",
        "with his feet",
        "with her foot",
        "with his foot",
        "feet on penis",
        "foot on cock",
        "toes on cock",
        "feet stimulation",
        "foot fetish",
        "foot sex",
        "sex with feet",
        "sex with her feet",
        "feet play",
        "足交"
    ],
    "paizuri": [
        "titjob",
        "breast sex",
        "tit fuck",
        "titfuck",
        "between breasts",
        "乳交"
    ],
    "vaginal_masturbation": [
        "clit",
        "clitoris",
        "finger pussy",
        "fingering vagina",
        "pussy fingering",
        "vibrator",
        "dildo",
        "阴道自慰",
        "阴蒂",
        "插阴道",
        "震动棒"
    ],
    "breast_masturbation": [
        "nipple",
        "nipples",
        "pinch nipples",
        "touch breasts",
        "breast play",
        "乳房自慰",
        "乳头",
        "玩乳头",
        "摸胸"
    ]
}


def get_synonyms(pose_key: str) -> list:
    """获取姿势的同义词列表"""
    return POSE_SYNONYMS.get(pose_key, [])


# 匹配时忽略的代词和冠词
_STOP_WORDS = {'her', 'his', 'a', 'an', 'the', 'their', 'its', 'my', 'your', 'our'}


def _normalize(text: str) -> str:
    """去除代词/冠词，便于宽松匹配"""
    return ' '.join(w for w in text.lower().split() if w not in _STOP_WORDS)


def expand_query(query: str) -> str:
    """扩展查询，添加同义词"""
    query_lower = query.lower()
    query_normalized = _normalize(query_lower)
    expanded_terms = [query_lower]

    # 检查是否包含同义词（支持精确匹配和去代词匹配）
    for pose_key, synonyms in POSE_SYNONYMS.items():
        for synonym in synonyms:
            synonym_lower = synonym.lower()
            synonym_normalized = _normalize(synonym_lower)
            if synonym_lower in query_lower or synonym_normalized in query_normalized:
                # 添加姿势key和其他同义词
                expanded_terms.append(pose_key)
                expanded_terms.extend(synonyms)
                break

    return ' '.join(set(expanded_terms))
