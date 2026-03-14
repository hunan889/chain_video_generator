# 同义词工具快速参考

## 一句话总结
**不需要手动处理每个LORA，一条命令自动处理所有LORA**

---

## 常用命令

### 初次使用（一次性）
```bash
# 处理所有LORA
python scripts/improve_lora_metadata.py --apply

# 重建索引
curl -X POST http://localhost:8000/api/v1/admin/embeddings/rebuild-loras \
  -H "X-API-Key: wan22-default-key-change-me"
```

### 新增LORA后
```bash
# 重新运行工具即可
python scripts/improve_lora_metadata.py --apply
```

### 发现新同义词时
```bash
# 1. 分析搜索日志
python scripts/discover_synonyms.py

# 2. 如果有建议，编辑 improve_lora_metadata.py 添加1行

# 3. 重新运行
python scripts/improve_lora_metadata.py --apply
```

### 查看覆盖率
```bash
python scripts/discover_synonyms.py --report
```

---

## 维护频率

| 任务 | 频率 | 耗时 |
|------|------|------|
| 初次设置 | 一次性 | 5分钟 |
| 新增LORA | 按需 | 10秒 |
| 发现新同义词 | 每月（可选） | 5分钟 |

---

## 工具文件

| 文件 | 用途 |
|------|------|
| `scripts/improve_lora_metadata.py` | 主工具（规则引擎） |
| `scripts/improve_lora_metadata_ai.py` | AI版本（可选） |
| `scripts/discover_synonyms.py` | 从搜索日志学习 |

---

## 详细文档

- `SYNONYM_MAINTENANCE_GUIDE.md` - 完整维护指南
- `LORA_METADATA_IMPROVEMENT_GUIDE.md` - 方案对比
- `SEMANTIC_SEARCH_EXPLANATION.md` - 匹配原理
- `SEARCH_IMPROVEMENT_REPORT.md` - 改进效果

---

## 核心理念

```
配置规则 → 批量处理 → 自动学习
   ↓           ↓           ↓
  1次        所有LORA    用户行为
```

**不是**: 每个LORA手动处理
**而是**: 一次配置，自动批量处理
