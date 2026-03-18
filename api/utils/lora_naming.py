"""
LORA命名规范化工具
"""
import re


def normalize_lora_name(name: str) -> str:
    """
    标准化LORA名称，去除HIGH/LOW噪声阶段后缀

    统一规则：
    - 移除 high/low + noise 组合
    - 移除 highnoise/lownoise
    - 移除结尾的 _high/_low/_HIGH/_LOW
    - 清理多余的下划线和连字符
    """
    if not name:
        return ""

    # 移除常见的noise标识
    patterns = [
        r'[_-]?(high|low)[_-]?noise',
        r'[_-]?(highnoise|lownoise)',
        r'[_-](high|low|h|l)([_-]v?\d+)?$',
    ]

    normalized = name
    for pattern in patterns:
        normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)

    # 清理多余的下划线和连字符
    normalized = re.sub(r'[_-]+', '_', normalized)
    normalized = normalized.strip('_-')

    return normalized
