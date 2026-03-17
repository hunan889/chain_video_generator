"""
AI内容建议服务 - 为资源和LORA生成search_keywords和trigger_prompt
"""
import json
from typing import Dict, Optional
from openai import AsyncOpenAI


class ContentSuggester:
    """内容建议器 - 使用LLM分析资源并生成关键词和触发提示词"""

    def __init__(self, base_url: str = "http://localhost:20001/v1", api_key: str = "sk-xxx"):
        """初始化建议器

        Args:
            base_url: Qwen API地址
            api_key: API密钥（本地部署可随意）
        """
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = "/home/gime/soft/Qwen3-14B-v2-Abliterated"

    async def suggest_for_resource(self, resource: Dict) -> Dict:
        """为图片/视频资源生成search_keywords

        Args:
            resource: 资源字典，包含prompt, tags等

        Returns:
            {
                "search_keywords": "woman, bedroom, intimate, sensual",
                "reasoning": "基于prompt和标签提取的核心关键词"
            }
        """
        prompt = self._build_resource_prompt(resource)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._get_resource_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=300
            )

            result_text = response.choices[0].message.content.strip()
            return self._parse_resource_result(result_text)

        except Exception as e:
            return {
                "search_keywords": "",
                "reasoning": f"生成失败: {str(e)}"
            }

    async def suggest_for_lora(self, lora: Dict) -> Dict:
        """为LORA生成search_keywords和trigger_prompt

        Args:
            lora: LORA字典，包含name, description, tags等

        Returns:
            {
                "search_keywords": "orgasm, climax, pleasure, intense",
                "trigger_prompt": "woman experiencing intense orgasm, eyes closed, mouth open",
                "reasoning": "基于LORA功能生成的搜索关键词和触发提示词"
            }
        """
        prompt = self._build_lora_prompt(lora)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._get_lora_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )

            result_text = response.choices[0].message.content.strip()
            return self._parse_lora_result(result_text)

        except Exception as e:
            return {
                "search_keywords": "",
                "trigger_prompt": "",
                "reasoning": f"生成失败: {str(e)}"
            }

    def _get_resource_system_prompt(self) -> str:
        """获取资源分析系统提示词"""
        return """你是一个专业的内容分析助手。你的任务是分析图片/视频资源的prompt和标签，生成自然语言格式的搜索关键词。

**任务要求**：
1. 从prompt中提取最重要的视觉元素和动作
2. 结合标签信息（包括标签分类），提炼出核心描述
3. **重要**：生成自然语言句子，而不是逗号分隔的单词列表
4. 优先提取：人物特征、动作(action)、体位(position)、身体部位(body_parts)、场景(scene)、情绪(expression)
5. 标签分类说明：
   - action: 动作类（如blowjob, kissing, penetration）
   - position: 体位类（如doggy, cowgirl, missionary）
   - body_parts: 身体部位（如breasts, pussy, ass）
   - scene: 场景类（如bedroom, bathroom, outdoor）
   - expression: 表情类（如moaning, orgasm, pleasure）
   - clothing: 服装类（如lingerie, naked, dress）
   - modifier: 修饰词（如intense, gentle, rough）

**输出格式**（必须严格遵守JSON格式）：
{
    "search_keywords": "A woman in bedroom engaged in intimate activity, sensual atmosphere with soft lighting",
    "reasoning": "基于prompt和标签（action, scene, expression）生成的自然语言描述"
}

只返回JSON，不要有其他文字。"""

    def _get_lora_system_prompt(self) -> str:
        """获取LORA分析系统提示词"""
        return """你是一个专业的LORA分析助手。你的任务是为LORA生成搜索关键词和触发提示词。

**任务要求**：
1. **search_keywords**: 自然语言描述，用于语义搜索
   - 描述LORA的主要功能和特征
   - 使用完整的短语或句子，而不是逗号分隔的单词
   - 30-80个单词
   - 例如：woman experiencing intense orgasm with eyes closed and mouth open, body trembling with pleasure

2. **trigger_prompt**: 一句完整的英文提示词，用于触发LORA效果
   - 描述LORA的典型使用场景
   - 包含关键视觉元素和动作
   - 30-80个单词
   - 例如：woman in doggy style position, bent over with hands on knees, man behind her

**输出格式**（必须严格遵守JSON格式）：
{
    "search_keywords": "woman experiencing intense orgasm with eyes closed and mouth open, body trembling with pleasure and ecstatic expression",
    "trigger_prompt": "woman experiencing intense orgasm, eyes closed, mouth open, body trembling, ecstatic expression",
    "reasoning": "基于LORA的trigger_words和描述生成自然语言格式"
}

只返回JSON，不要有其他文字。"""

    def _build_resource_prompt(self, resource: Dict) -> str:
        """构建资源分析提示词"""
        prompt_text = resource.get('prompt', '')
        tags = resource.get('tags', [])

        # 提取标签名称和分类
        tags_by_category = {}
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    tag_name = tag.get('name', '')
                    tag_category = tag.get('category', 'other')
                    if tag_category not in tags_by_category:
                        tags_by_category[tag_category] = []
                    tags_by_category[tag_category].append(tag_name)

        # 构建分类标签字符串
        tags_str = ""
        for category, tag_list in tags_by_category.items():
            tags_str += f"\n  - {category}: {', '.join(tag_list)}"

        prompt = f"""请分析以下资源并生成自然语言格式的搜索关键词：

**Prompt**: {prompt_text[:500] if prompt_text else '无'}
**标签（按分类）**: {tags_str if tags_str else '无'}

请生成search_keywords（自然语言句子格式，JSON格式输出）："""

        return prompt

    def _build_lora_prompt(self, lora: Dict) -> str:
        """构建LORA分析提示词"""
        name = lora.get('name', '')
        description = lora.get('description', '')
        tags = lora.get('tags', [])
        trigger_words = lora.get('trigger_words', [])

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

        prompt = f"""请分析以下LORA并生成搜索关键词和触发提示词：

**名称**: {name}
**描述**: {description or '无'}
**标签**: {', '.join(tags) if tags else '无'}
**触发词**: {', '.join(trigger_words) if trigger_words else '无'}

请生成search_keywords和trigger_prompt（JSON格式）："""

        return prompt

    def _parse_resource_result(self, result_text: str) -> Dict:
        """解析资源分析结果"""
        try:
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = result_text[start:end]
                result = json.loads(json_str)
                return result
            else:
                raise ValueError("未找到JSON格式")
        except Exception as e:
            return {
                "search_keywords": "",
                "reasoning": f"解析失败: {str(e)}\n原始输出: {result_text}"
            }

    def _parse_lora_result(self, result_text: str) -> Dict:
        """解析LORA分析结果"""
        try:
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = result_text[start:end]
                result = json.loads(json_str)
                return result
            else:
                raise ValueError("未找到JSON格式")
        except Exception as e:
            return {
                "search_keywords": "",
                "trigger_prompt": "",
                "reasoning": f"解析失败: {str(e)}\n原始输出: {result_text}"
            }


# 单例
_suggester_instance = None

def get_content_suggester() -> ContentSuggester:
    """获取内容建议器单例"""
    global _suggester_instance
    if _suggester_instance is None:
        _suggester_instance = ContentSuggester()
    return _suggester_instance
