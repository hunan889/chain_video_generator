"""
LORA分类服务 - 使用Qwen3-14B分析LORA并建议分类
"""
import asyncio
import json
from typing import Dict, List, Optional
from openai import AsyncOpenAI


class LoraClassifier:
    """LORA分类器 - 使用LLM分析LORA元数据并建议分类"""

    CATEGORIES = [
        "action",      # 动作类（性行为、运动）
        "body",        # 身体部位、体型
        "scene",       # 场景、环境
        "style",       # 风格、画风
        "position",    # 姿势、体位
        "modifier"     # 修饰词、质量增强
    ]

    def __init__(self, base_url: str = "http://localhost:20001/v1", api_key: str = "sk-xxx"):
        """初始化分类器

        Args:
            base_url: Qwen API地址
            api_key: API密钥（本地部署可随意）
        """
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = "/home/gime/soft/Qwen3-14B-v2-Abliterated"

    async def classify(self, lora: Dict) -> Dict:
        """分类单个LORA

        Args:
            lora: LORA元数据字典，包含name, description, tags, trigger_words等

        Returns:
            {
                "category": "action",
                "confidence": 0.95,
                "reasoning": "这个LORA主要用于生成性行为动作...",
                "alternative": {"category": "position", "confidence": 0.3}
            }
        """
        prompt = self._build_prompt(lora)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )

            result_text = response.choices[0].message.content.strip()
            return self._parse_result(result_text)

        except Exception as e:
            return {
                "category": None,
                "confidence": 0.0,
                "reasoning": f"分类失败: {str(e)}",
                "alternative": None
            }

    async def batch_classify(self, loras: List[Dict], batch_size: int = 5) -> List[Dict]:
        """批量分类LORA

        Args:
            loras: LORA列表
            batch_size: 并发数

        Returns:
            分类结果列表
        """
        results = []
        for i in range(0, len(loras), batch_size):
            batch = loras[i:i+batch_size]
            batch_results = await asyncio.gather(*[
                self.classify(lora) for lora in batch
            ])
            results.extend(batch_results)

            # 避免请求过快
            if i + batch_size < len(loras):
                await asyncio.sleep(0.5)

        return results

    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return f"""你是一个专业的LORA分类助手。你的任务是分析LORA的元数据，并将其归类到以下6个类别之一：

**类别定义**：
1. action - 动作类：性行为、运动、动态动作（如orgasm, blowjob, riding, kissing）
2. body - 身体类：身体部位、体型特征（如big_breasts, muscular, body_type）
3. scene - 场景类：环境、背景、地点（如bedroom, outdoor, studio）
4. style - 风格类：画风、艺术风格、渲染风格（如realistic, anime, 3d）
5. position - 姿势类：体位、姿态、角度（如cowgirl, doggy_style, missionary）
6. modifier - 修饰类：质量增强、细节修饰、通用增强（如quality, detailed, lighting）

**分析要点**：
- 优先看trigger_words和tags，它们最能体现LORA的用途
- description提供上下文信息
- 文件名有时包含noise_stage信息（high/low），但不影响分类
- 如果LORA同时涉及多个类别，选择最主要的那个

**输出格式**（必须严格遵守JSON格式）：
{{
    "category": "action",
    "confidence": 0.95,
    "reasoning": "这个LORA的trigger_words包含'orgasm', 'climax'等动作词，主要用于生成性高潮动作场景",
    "alternative": {{"category": "position", "confidence": 0.3}}
}}

只返回JSON，不要有其他文字。"""

    def _build_prompt(self, lora: Dict) -> str:
        """构建分类提示词"""
        name = lora.get('name', '')
        description = lora.get('description', '')
        tags = lora.get('tags', [])
        trigger_words = lora.get('trigger_words', [])
        file = lora.get('file', '')

        # 解析tags和trigger_words（可能是JSON字符串）
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except:
                tags = []

        if isinstance(trigger_words, str):
            try:
                trigger_words = json.loads(trigger_words)
            except:
                trigger_words = []

        prompt = f"""请分析以下LORA并给出分类建议：

**名称**: {name}
**文件**: {file}
**描述**: {description or '无'}
**标签**: {', '.join(tags) if tags else '无'}
**触发词**: {', '.join(trigger_words) if trigger_words else '无'}

请给出分类结果（JSON格式）："""

        return prompt

    def _parse_result(self, result_text: str) -> Dict:
        """解析LLM返回结果"""
        try:
            # 尝试提取JSON
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = result_text[start:end]
                result = json.loads(json_str)

                # 验证category是否有效
                if result.get('category') not in self.CATEGORIES:
                    result['category'] = None

                return result
            else:
                raise ValueError("未找到JSON格式")

        except Exception as e:
            return {
                "category": None,
                "confidence": 0.0,
                "reasoning": f"解析失败: {str(e)}\n原始输出: {result_text}",
                "alternative": None
            }


# 单例
_classifier_instance = None

def get_lora_classifier() -> LoraClassifier:
    """获取LORA分类器单例"""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = LoraClassifier()
    return _classifier_instance
