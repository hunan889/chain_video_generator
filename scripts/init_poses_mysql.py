"""Create poses tables in MySQL and seed initial data."""
import pymysql
import os

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', '10.200.0.21'),
    'port': int(os.getenv('MYSQL_PORT', '3306')),
    'user': os.getenv('MYSQL_USER', 'user_soga'),
    'password': os.getenv('MYSQL_PASSWORD', '1IvO@*#68'),
    'database': os.getenv('MYSQL_DB', 'tudou_soga'),
    'charset': 'utf8mb4',
    'connect_timeout': 10,
}

POSES = {
    'against_wall': ('Against Wall', '壁式', 'position'),
    'anal': ('Anal', '肛交', 'position'),
    'blowjob': ('Blowjob', '口交', 'oral'),
    'bondage': ('Bondage', '捆绑', 'other'),
    'breast_masturbation': ('Breast Masturbation', '乳房自慰', 'masturbation'),
    'breast_play': ('Breast Play', '玩弄乳房', 'other'),
    'bukkake': ('Bukkake', '颜射群交', 'other'),
    'cowgirl': ('Cowgirl', '女上位', 'position'),
    'deepthroat': ('Deepthroat', '深喉', 'oral'),
    'doggy': ('Doggy Style', '后入式', 'position'),
    'face_down_ass_up': ('Face Down Ass Up', '趴跪式', 'position'),
    'facial': ('Facial', '颜射', 'other'),
    'fingering': ('Fingering', '手指插入', 'masturbation'),
    'footjob': ('Footjob', '足交', 'other'),
    'gangbang': ('Gangbang', '群交', 'other'),
    'handjob': ('Handjob', '手淫', 'other'),
    'lotus': ('Lotus', '莲花式', 'position'),
    'missionary': ('Missionary', '传教士', 'position'),
    'paizuri': ('Paizuri', '乳交', 'other'),
    'reverse_cowgirl': ('Reverse Cowgirl', '反骑', 'position'),
    'strap_on': ('Strap On', '穿戴式', 'other'),
    'threesome': ('Threesome', '三人行', 'other'),
    'vaginal_masturbation': ('Vaginal Masturbation', '自慰', 'masturbation'),
}


def main():
    conn = pymysql.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()

    # 1. Create tables (IF NOT EXISTS — safe to rerun)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS poses (
        id INT AUTO_INCREMENT PRIMARY KEY,
        pose_key VARCHAR(100) NOT NULL UNIQUE,
        name_en VARCHAR(200) NOT NULL DEFAULT '',
        name_cn VARCHAR(200) NOT NULL DEFAULT '',
        description TEXT,
        difficulty VARCHAR(50) DEFAULT 'medium',
        category VARCHAR(50) DEFAULT 'other',
        enabled TINYINT(1) DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    print('Created: poses')

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pose_reference_images (
        id INT AUTO_INCREMENT PRIMARY KEY,
        pose_id INT NOT NULL,
        image_url VARCHAR(500) NOT NULL,
        angle VARCHAR(50) DEFAULT NULL,
        style VARCHAR(50) DEFAULT 'realistic',
        prompt TEXT,
        model VARCHAR(100) DEFAULT NULL,
        is_default TINYINT(1) DEFAULT 0,
        quality_score FLOAT DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_pose_id (pose_id),
        INDEX idx_pri_pose_id (pose_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    print('Created: pose_reference_images')

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pose_loras (
        id INT AUTO_INCREMENT PRIMARY KEY,
        pose_id INT NOT NULL,
        lora_id INT DEFAULT NULL,
        lora_name VARCHAR(200) DEFAULT NULL,
        lora_type VARCHAR(20) NOT NULL DEFAULT 'video',
        noise_stage VARCHAR(20) DEFAULT 'high',
        trigger_words TEXT,
        trigger_prompt TEXT,
        recommended_weight FLOAT DEFAULT 0.8,
        is_default TINYINT(1) DEFAULT 0,
        sort_order INT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_pl_pose_id (pose_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    print('Created: pose_loras')

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pose_prompt_templates (
        id INT AUTO_INCREMENT PRIMARY KEY,
        pose_id INT NOT NULL,
        angle VARCHAR(50) DEFAULT NULL,
        template TEXT NOT NULL,
        priority INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_pt_pose_id (pose_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    print('Created: pose_prompt_templates')

    conn.commit()

    # 2. Seed initial poses
    inserted = 0
    for key, (en, cn, cat) in POSES.items():
        try:
            cursor.execute(
                'INSERT INTO poses (pose_key, name_en, name_cn, category) VALUES (%s, %s, %s, %s)',
                (key, en, cn, cat)
            )
            inserted += 1
        except pymysql.err.IntegrityError:
            pass

    conn.commit()
    print(f'Inserted {inserted} poses')

    cursor.execute('SELECT COUNT(*) FROM poses')
    print(f'Total poses: {cursor.fetchone()[0]}')

    conn.close()
    print('Done.')


if __name__ == '__main__':
    main()
