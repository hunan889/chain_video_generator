# LORA元数据改进方案对比

## 三种方案

### 方案1: 手动编辑（不推荐）
**操作**: 直接在数据库或管理界面中手动编辑每个LORA的标签

**优点**:
- 完全控制
- 精确度最高

**缺点**:
- ❌ 非常耗时（100个LORA需要数小时）
- ❌ 容易遗漏
- ❌ 不一致性
- ❌ 无法批量处理

**适用场景**: 只有少量LORA需要微调

---

### 方案2: 规则引擎（当前实现，推荐）
**文件**: `scripts/improve_lora_metadata.py`

**操作**:
```bash
# 一键处理所有LORA
python scripts/improve_lora_metadata.py --apply
```

**工作原理**:
1. 预定义同义词库（如"doggy" → ["doggy style", "on all fours", ...]）
2. 自动检测LORA名称中的关键词
3. 自动添加对应的同义词
4. 批量处理

**优点**:
- ✅ 完全自动化
- ✅ 快速（几秒处理所有LORA）
- ✅ 一致性好
- ✅ 无需API key或外部服务
- ✅ 可预测的结果

**缺点**:
- ⚠️ 只能识别预定义的关键词
- ⚠️ 需要维护同义词库

**当前同义词库覆盖**:
- ✅ 常见姿势: doggy, cowgirl, missionary, standing, spooning, suspended
- ✅ 身体特征: big_breasts, small_breasts, thick, slim
- ✅ 动作: blowjob, handjob, titfuck, anal

**适用场景**:
- 大部分LORA名称包含常见关键词
- 需要快速批量处理
- 不想依赖外部服务

---

### 方案3: AI驱动（最智能，可选）
**文件**: `scripts/improve_lora_metadata_ai.py`

**操作**:
```bash
# 使用本地规则（fallback到方案2）
python scripts/improve_lora_metadata_ai.py --apply

# 使用本地Ollama（免费）
python scripts/improve_lora_metadata_ai.py --ai=ollama --apply

# 使用OpenAI（需要API key）
python scripts/improve_lora_metadata_ai.py --ai=openai --apply
```

**工作原理**:
1. 读取LORA的名称、描述、现有标签
2. 发送给LLM生成同义词
3. LLM理解语义，生成相关词汇
4. 自动应用到数据库

**优点**:
- ✅ 最智能，能理解任何LORA
- ✅ 无需预定义词库
- ✅ 能处理特殊/罕见的LORA
- ✅ 生成更丰富的同义词

**缺点**:
- ⚠️ 需要配置（Ollama或OpenAI）
- ⚠️ 较慢（每个LORA需要1-2秒）
- ⚠️ OpenAI需要付费
- ⚠️ 结果不完全可预测

**适用场景**:
- LORA名称很特殊，规则引擎无法识别
- 需要最高质量的同义词
- 愿意花时间配置AI服务

---

## 推荐流程

### 第一步: 使用规则引擎（方案2）
```bash
# 预览改进
python scripts/improve_lora_metadata.py

# 应用改进
python scripts/improve_lora_metadata.py --apply

# 重建索引
curl -X POST http://localhost:8000/api/v1/admin/embeddings/rebuild-loras \
  -H "X-API-Key: wan22-default-key-change-me"
```

**预期结果**: 80-90%的LORA会被正确处理

### 第二步: 检查未识别的LORA
```bash
# 查看哪些LORA没有category（说明未被识别）
mysql -h ... -e "SELECT id, name FROM lora_metadata WHERE category IS NULL"
```

### 第三步: 手动处理特殊LORA（可选）
对于规则引擎无法识别的LORA，有两个选择：

**选项A**: 扩展规则引擎
```python
# 在 improve_lora_metadata.py 中添加新规则
POSITION_SYNONYMS = {
    # ... 现有规则
    'special_pose': ['synonym1', 'synonym2', ...],  # 添加新规则
}
```

**选项B**: 使用AI处理特定LORA
```bash
# 只对特定LORA使用AI
python scripts/improve_lora_metadata_ai.py 123 --ai=ollama --apply
```

---

## 实际示例

### 示例1: 常见LORA（规则引擎完美处理）

**输入**:
```
Name: wan_22_doggy_by_mq_lab
Tags: ["concept", "sex", "nsfw", "doggy"]
```

**规则引擎输出**:
```
Tags: ["concept", "sex", "nsfw", "doggy", "doggy style",
       "from behind", "on all fours", "rear entry", ...]
Category: position
Trigger: "doggy style, from behind, on all fours"
```

**处理时间**: < 0.1秒

---

### 示例2: 特殊LORA（规则引擎无法识别）

**输入**:
```
Name: custom_animation_v2_final
Description: "Special animation for character movement"
Tags: []
```

**规则引擎输出**:
```
Tags: []  # 无法识别
Category: other
Trigger: null
```

**AI输出**:
```
Tags: ["animation", "movement", "motion", "character animation",
       "custom animation", "animated", "moving", ...]
Category: style
Trigger: "custom character animation and movement"
```

**处理时间**: 1-2秒

---

## 成本对比

| 方案 | 100个LORA的成本 | 时间 |
|------|----------------|------|
| 手动编辑 | 人工成本（数小时） | 3-5小时 |
| 规则引擎 | 免费 | 5秒 |
| AI (Ollama) | 免费（需要本地GPU） | 2-3分钟 |
| AI (OpenAI) | ~$0.50-1.00 | 2-3分钟 |

---

## 总结

### 推荐方案: 规则引擎（方案2）

**理由**:
1. ✅ 完全自动化，一键处理
2. ✅ 免费，无需配置
3. ✅ 快速（几秒完成）
4. ✅ 覆盖80-90%的常见场景
5. ✅ 结果可预测和一致

**使用方法**:
```bash
# 就这一条命令！
python scripts/improve_lora_metadata.py --apply
```

### 何时使用AI方案

只在以下情况考虑AI方案：
- 规则引擎处理后，仍有大量LORA未被识别
- LORA名称非常特殊或专业
- 需要最高质量的同义词生成

---

## 维护同义词库

如果发现规则引擎遗漏了某些常见词汇，可以轻松扩展：

```python
# 编辑 scripts/improve_lora_metadata.py

POSITION_SYNONYMS = {
    # 添加新的姿势
    'lotus': ['lotus position', 'sitting lotus', 'tantric position'],
    'amazon': ['amazon position', 'woman dominant', 'reverse missionary'],

    # 扩展现有姿势
    'doggy': [
        'doggy style', 'from behind', 'on all fours',
        'rear entry', 'doggystyle', 'doggy position',
        # 添加更多同义词
        'back entry', 'prone position', 'all fours position'
    ],
}
```

**维护频率**: 每月检查一次，添加新发现的常见词汇

---

## 快速开始

```bash
# 1. 运行规则引擎（推荐）
python scripts/improve_lora_metadata.py --apply

# 2. 重建索引
curl -X POST http://localhost:8000/api/v1/admin/embeddings/rebuild-loras \
  -H "X-API-Key: wan22-default-key-change-me"

# 3. 测试搜索
curl -X POST http://localhost:8000/api/v1/search/loras \
  -H "X-API-Key: wan22-default-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"query": "on all fours", "top_k": 3}'

# 完成！
```
