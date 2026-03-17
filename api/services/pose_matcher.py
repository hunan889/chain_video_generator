"""
姿势匹配服务
从用户输入中匹配姿势，返回完整配置
"""
import sqlite3
import pymysql
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "wan22.db"

# MySQL数据库配置（用于LORA数据）
MYSQL_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


@dataclass
class PoseMatch:
    """姿势匹配结果"""
    pose_id: int
    pose_key: str
    name_en: str
    name_cn: str
    description: str
    difficulty: str
    category: str
    match_score: float
    matched_keywords: List[str]
    confidence: str  # high/medium/low


@dataclass
class PoseConfig:
    """姿势完整配置"""
    pose: Dict
    reference_images: List[Dict]
    image_loras: List[Dict]
    video_loras: List[Dict]
    prompt_templates: List[Dict]


class PoseMatcher:
    """姿势匹配器"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)

    def _get_connection(self):
        """获取SQLite数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_mysql_connection(self):
        """获取MySQL数据库连接（用于LORA数据）"""
        return pymysql.connect(**MYSQL_CONFIG)

    def _tokenize(self, text: str) -> List[str]:
        """分词和标准化"""
        # 简单分词：按空格分割，转小写
        tokens = text.lower().split()

        # 生成n-gram (1-4词组合)
        ngrams = []
        for n in range(1, 5):
            for i in range(len(tokens) - n + 1):
                ngram = ' '.join(tokens[i:i+n])
                ngrams.append(ngram)

        return ngrams

    def match_poses(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.3
    ) -> List[PoseMatch]:
        """
        从用户输入中匹配姿势

        Args:
            query: 用户输入
            top_k: 返回top K个结果
            min_score: 最低匹配分数

        Returns:
            匹配的姿势列表
        """
        tokens = self._tokenize(query)

        if not tokens:
            return []

        conn = self._get_connection()
        cursor = conn.cursor()

        # 查询匹配的关键词
        placeholders = ','.join('?' * len(tokens))
        cursor.execute(f"""
        SELECT
            p.id, p.pose_key, p.name_en, p.name_cn, p.description,
            p.difficulty, p.category,
            pk.keyword, pk.keyword_type, pk.weight
        FROM poses p
        JOIN pose_keywords pk ON p.id = pk.pose_id
        WHERE pk.keyword IN ({placeholders})
        AND p.enabled = 1
        """, tokens)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            logger.info(f"未找到匹配的姿势: {query}")
            return []

        # 计算每个姿势的匹配分数
        pose_scores = {}
        for row in rows:
            pose_id = row['id']

            if pose_id not in pose_scores:
                pose_scores[pose_id] = {
                    'pose_id': pose_id,
                    'pose_key': row['pose_key'],
                    'name_en': row['name_en'],
                    'name_cn': row['name_cn'],
                    'description': row['description'],
                    'difficulty': row['difficulty'],
                    'category': row['category'],
                    'score': 0.0,
                    'matched_keywords': []
                }

            # 累加权重
            pose_scores[pose_id]['score'] += row['weight']
            pose_scores[pose_id]['matched_keywords'].append(row['keyword'])

        # 转换为PoseMatch对象
        matches = []
        for data in pose_scores.values():
            # 归一化分数（除以匹配的关键词数量）
            normalized_score = data['score'] / len(data['matched_keywords'])

            # 判断置信度
            if normalized_score >= 0.8:
                confidence = 'high'
            elif normalized_score >= 0.5:
                confidence = 'medium'
            else:
                confidence = 'low'

            if normalized_score >= min_score:
                matches.append(PoseMatch(
                    pose_id=data['pose_id'],
                    pose_key=data['pose_key'],
                    name_en=data['name_en'],
                    name_cn=data['name_cn'],
                    description=data['description'],
                    difficulty=data['difficulty'],
                    category=data['category'],
                    match_score=normalized_score,
                    matched_keywords=data['matched_keywords'],
                    confidence=confidence
                ))

        # 排序
        matches.sort(key=lambda x: x.match_score, reverse=True)

        logger.info(f"查询: {query}, 匹配到 {len(matches)} 个姿势")
        for match in matches[:3]:
            logger.info(f"  - {match.pose_key}: {match.match_score:.3f} ({match.confidence})")

        return matches[:top_k]

    def get_pose_config(
        self,
        pose_id: int,
        preferences: Optional[Dict] = None
    ) -> Optional[PoseConfig]:
        """
        获取姿势的完整配置

        Args:
            pose_id: 姿势ID
            preferences: 偏好设置 {
                "angle": "pov",
                "style": "realistic",
                "noise_stage": "high"
            }

        Returns:
            姿势配置
        """
        preferences = preferences or {}
        angle = preferences.get('angle')
        style = preferences.get('style')
        noise_stage = preferences.get('noise_stage', 'high')

        conn = self._get_connection()
        cursor = conn.cursor()

        # 1. 获取姿势基本信息
        cursor.execute("SELECT * FROM poses WHERE id = ?", (pose_id,))
        pose_row = cursor.fetchone()
        if not pose_row:
            conn.close()
            return None

        pose = dict(pose_row)

        # 2. 获取首帧图
        query = "SELECT * FROM pose_reference_images WHERE pose_id = ?"
        params = [pose_id]

        if angle or style:
            conditions = []
            if angle:
                conditions.append("(angle = ? OR is_default = 1)")
                params.append(angle)
            if style:
                conditions.append("(style = ? OR style IS NULL)")
                params.append(style)

            query += " AND " + " AND ".join(conditions)

        query += " ORDER BY is_default DESC, quality_score DESC"

        cursor.execute(query, params)
        reference_images = [dict(row) for row in cursor.fetchall()]

        # 3. 获取图片LORA（从SQLite获取基本信息）
        cursor.execute("""
        SELECT pl.*
        FROM pose_loras pl
        WHERE pl.pose_id = ? AND pl.lora_type = 'image'
        ORDER BY pl.is_default DESC, pl.recommended_weight DESC
        """, (pose_id,))
        image_loras = [dict(row) for row in cursor.fetchall()]

        # 4. 获取视频LORA（从SQLite获取基本信息）
        cursor.execute("""
        SELECT pl.*
        FROM pose_loras pl
        WHERE pl.pose_id = ? AND pl.lora_type = 'video'
        ORDER BY
            CASE WHEN pl.noise_stage = ? THEN 0 ELSE 1 END,
            pl.is_default DESC,
            pl.noise_stage
        """, (pose_id, noise_stage))
        video_loras = [dict(row) for row in cursor.fetchall()]

        conn.close()

        # 5. 从MySQL获取LORA的preview_url和civitai_id
        lora_ids = []
        for lora in image_loras + video_loras:
            if lora.get('lora_id'):
                lora_ids.append(lora['lora_id'])

        if lora_ids:
            try:
                mysql_conn = self._get_mysql_connection()
                mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)

                placeholders = ','.join(['%s'] * len(lora_ids))
                mysql_cursor.execute(f"""
                SELECT id, name, preview_url, civitai_id
                FROM lora_metadata
                WHERE id IN ({placeholders})
                """, lora_ids)

                lora_data = {row['id']: row for row in mysql_cursor.fetchall()}
                mysql_cursor.close()
                mysql_conn.close()

                # 合并LORA数据
                for lora in image_loras + video_loras:
                    lora_id = lora.get('lora_id')
                    if lora_id and lora_id in lora_data:
                        # 从MySQL获取完整数据
                        lora['lora_name'] = lora_data[lora_id]['name']
                        lora['preview_url'] = lora_data[lora_id]['preview_url']
                        lora['civitai_id'] = lora_data[lora_id]['civitai_id']
                    elif not lora_id and lora.get('lora_name'):
                        # 如果没有lora_id，使用SQLite中存储的lora_name
                        # 这些LORA没有preview_url和civitai_id
                        pass
            except Exception as e:
                logger.error(f"Failed to fetch LORA data from MySQL: {e}")

        # 6. 获取提示词模板
        conn = self._get_connection()
        cursor = conn.cursor()
        query = """
        SELECT * FROM pose_prompt_templates
        WHERE pose_id = ?
        """
        params = [pose_id]

        if angle:
            query += " AND (angle = ? OR angle IS NULL)"
            params.append(angle)

        query += " ORDER BY priority DESC, angle IS NOT NULL DESC"

        cursor.execute(query, params)
        prompt_templates = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return PoseConfig(
            pose=pose,
            reference_images=reference_images,
            image_loras=image_loras,
            video_loras=video_loras,
            prompt_templates=prompt_templates
        )

    def list_all_poses(self, category: Optional[str] = None) -> List[Dict]:
        """
        列出所有姿势

        Args:
            category: 分类筛选

        Returns:
            姿势列表（包含统计信息和关键词）
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
        SELECT p.*,
               (SELECT COUNT(*) FROM pose_keywords WHERE pose_id = p.id) as keyword_count,
               (SELECT COUNT(*) FROM pose_reference_images WHERE pose_id = p.id) as reference_image_count,
               (SELECT COUNT(*) FROM pose_loras WHERE pose_id = p.id) as lora_count
        FROM poses p
        WHERE p.enabled = 1
        """
        params = []

        if category:
            query += " AND p.category = ?"
            params.append(category)

        query += " ORDER BY p.difficulty, p.pose_key"

        cursor.execute(query, params)
        poses = [dict(row) for row in cursor.fetchall()]

        # 为每个姿势添加关键词列表
        for pose in poses:
            cursor.execute("""
            SELECT keyword FROM pose_keywords
            WHERE pose_id = ?
            ORDER BY keyword
            """, (pose['id'],))
            keywords = [row[0] for row in cursor.fetchall()]
            pose['keywords'] = keywords

        conn.close()

        return poses


# 全局单例
_pose_matcher: Optional[PoseMatcher] = None


def get_pose_matcher() -> PoseMatcher:
    """获取姿势匹配器单例"""
    global _pose_matcher
    if _pose_matcher is None:
        _pose_matcher = PoseMatcher()
    return _pose_matcher
