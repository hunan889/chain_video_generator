"""
姿势配置服务
"""
import json
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
class PoseConfig:
    """姿势完整配置"""
    pose: Dict
    reference_images: List[Dict]
    image_loras: List[Dict]
    video_loras: List[Dict]
    prompt_templates: List[Dict]


class PoseMatcher:
    """姿势配置管理器"""

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

        # 3. 获取图片LORA（从SQLite获取基本信息，按sort_order排序）
        cursor.execute("""
        SELECT pl.*
        FROM pose_loras pl
        WHERE pl.pose_id = ? AND pl.lora_type = 'image'
        ORDER BY
            COALESCE(pl.sort_order, pl.id),
            pl.is_default DESC,
            pl.recommended_weight DESC
        """, (pose_id,))
        image_loras_raw = [dict(row) for row in cursor.fetchall()]

        # 标记前5个为enabled，其余为disabled
        image_loras = []
        for idx, lora in enumerate(image_loras_raw):
            lora['enabled'] = idx < 5
            lora['sort_index'] = idx
            image_loras.append(lora)

        # 4. 获取视频LORA（从SQLite获取基本信息，按sort_order排序）
        cursor.execute("""
        SELECT pl.*
        FROM pose_loras pl
        WHERE pl.pose_id = ? AND pl.lora_type = 'video'
        ORDER BY
            CASE WHEN pl.noise_stage = ? THEN 0 ELSE 1 END,
            COALESCE(pl.sort_order, pl.id),
            pl.is_default DESC,
            pl.noise_stage
        """, (pose_id, noise_stage))
        video_loras_raw = [dict(row) for row in cursor.fetchall()]

        # 标记前5个为enabled，其余为disabled
        video_loras = []
        for idx, lora in enumerate(video_loras_raw):
            lora['enabled'] = idx < 5
            lora['sort_index'] = idx
            video_loras.append(lora)

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
                SELECT id, name, file, preview_url, civitai_id, trigger_words, trigger_prompt
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
                        name = lora_data[lora_id]['name']
                        if not name and lora_data[lora_id].get('file'):
                            # 如果name为空，使用file字段
                            name = lora_data[lora_id]['file']
                        lora['lora_name'] = name
                        lora['preview_url'] = lora_data[lora_id]['preview_url']
                        lora['civitai_id'] = lora_data[lora_id]['civitai_id']
                        tw = lora_data[lora_id].get('trigger_words') or []
                        if isinstance(tw, str):
                            try:
                                tw = json.loads(tw)
                            except Exception:
                                tw = []
                        lora['trigger_words'] = tw
                        lora['trigger_prompt'] = lora_data[lora_id].get('trigger_prompt') or None
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

    def list_all_poses(self, category: Optional[str] = None, include_disabled: bool = False) -> List[Dict]:
        """
        列出所有姿势

        Args:
            category: 分类筛选
            include_disabled: 是否包含已禁用的姿势

        Returns:
            姿势列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
        SELECT p.*,
               (SELECT COUNT(*) FROM pose_reference_images WHERE pose_id = p.id) as reference_image_count,
               (SELECT COUNT(*) FROM pose_loras WHERE pose_id = p.id) as lora_count
        FROM poses p
        WHERE 1=1
        """
        params = []

        if not include_disabled:
            query += " AND p.enabled = 1"

        if category:
            query += " AND p.category = ?"
            params.append(category)

        query += " ORDER BY p.difficulty, p.pose_key"

        cursor.execute(query, params)
        poses = [dict(row) for row in cursor.fetchall()]
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
