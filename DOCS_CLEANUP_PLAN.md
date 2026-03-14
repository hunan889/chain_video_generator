# 文档整理计划

## 当前状态分析

共发现 **42个 Markdown 文件**，存在以下问题：
1. **重复内容**：多个文档描述相同功能
2. **过时文档**：临时设计文档未清理
3. **组织混乱**：文档分散在多个目录
4. **命名不规范**：大写文件名混杂

---

## 文档分类

### 核心文档（保留）
1. `README.md` - 项目主文档
2. `CLAUDE.md` - Claude Code 项目配置
3. `METHODOLOGY.md` - 工作方法论

### 用户文档（整合到 docs/）
**Story Mode 相关**：
- `docs/story-mode.md` - 索引文档（保留）
- `docs/story-mode-quickstart.md` - 快速开始
- `docs/story-mode-api-examples.md` - API 示例
- `docs/story-mode-optimization.md` - 性能优化

**其他用户文档**：
- `docs/quickstart.md` - 快速开始指南
- `docs/workflow-guide.md` - Workflow 指南
- `docs/advanced-workflow.md` - 高级 Workflow
- `docs/duration-guide.md` - 时长计算
- `docs/prompt-best-practices.md` - Prompt 最佳实践
- `docs/video-on-demand.md` - 视频点播
- `docs/query-tabs-guide.md` - 查询标签指南
- `docs/civitai-length-guide.md` - Civitai 长度指南

### 技术设计文档（移动到 docs/design/）
**LORA 推荐系统**：
- `LORA_RECOMMENDATION_DESIGN.md` - 系统设计
- `TECH_STACK_DECISION.md` - 技术选型（OpenAI）
- `TECH_STACK_DECISION_QWEN.md` - 技术选型（Qwen）
- `EMBEDDING_VS_LLM_COMPARISON.md` - Embedding vs LLM 对比
- `SEMANTIC_SEARCH_EXPLANATION.md` - 语义搜索原理
- `SEARCH_MECHANISM_FAQ.md` - 搜索机制 FAQ
- `NAME_WEIGHTING_SOLUTION.md` - 名称加权方案

**LORA 元数据改进**：
- `LORA_METADATA_IMPROVEMENT_GUIDE.md` - 改进指南
- `SYNONYM_MAINTENANCE_GUIDE.md` - 同义词维护
- `SYNONYM_CONFLICT_GUIDE.md` - 同义词冲突
- `SYNONYM_SYSTEM_SUMMARY.md` - 系统总结

**其他设计**：
- `VIDEO_GENERATION_WORKFLOW_PLAN_V2.md` - 视频生成工作流 V2
- `WORKFLOW_PARAMS_REDESIGN.md` - Workflow 参数重设计
- `SEARCH_KEYWORDS_DESIGN.md` - 搜索关键词设计

### 临时文档（删除或归档）
**Prompt 优化相关**：
- `PROMPT_OPTIMIZER_IMPROVEMENTS.md` - 已实现，可删除
- `DETAILED_EXPANSION_GUIDE.md` - 已实现，可删除

**快速参考**：
- `QUICK_START.md` - 与 `docs/quickstart.md` 重复
- `QUICK_REFERENCE.md` - 同义词工具快速参考，可合并

**TODO 和优化建议**：
- `TODO.md` - 任务清单（保留但需更新）
- `OPTIMIZATION_RECOMMENDATIONS.md` - 优化建议（可归档）

**测试和报告**：
- `STAGE_TESTING_SPEC.md` - 测试规范（移动到 docs/testing/）
- `docs/5090_SYNC_REPORT.md` - 同步报告（归档）

**脚本文档**：
- `scripts/SYNC_README.md` - 脚本说明（保留）

---

## 整理方案

### 第一步：创建新的目录结构

```
docs/
├── user-guide/           # 用户指南
│   ├── quickstart.md
│   ├── workflow-guide.md
│   ├── advanced-workflow.md
│   ├── prompt-best-practices.md
│   └── duration-guide.md
├── story-mode/           # Story Mode 专题
│   ├── README.md         # 索引
│   ├── quickstart.md
│   ├── api-examples.md
│   └── optimization.md
├── design/               # 技术设计文档
│   ├── lora-recommendation/
│   │   ├── system-design.md
│   │   ├── tech-stack.md
│   │   ├── semantic-search.md
│   │   └── metadata-improvement.md
│   ├── video-workflow/
│   │   └── workflow-v2.md
│   └── search/
│       ├── mechanism-faq.md
│       └── name-weighting.md
├── testing/              # 测试文档
│   └── stage-testing.md
└── archive/              # 归档文档
    ├── 5090-sync-report.md
    └── optimization-recommendations.md
```

### 第二步：合并重复文档

#### 合并 1: 快速开始指南
**目标文件**: `docs/user-guide/quickstart.md`
**合并来源**:
- `docs/quickstart.md`
- `QUICK_START.md`

**操作**: 保留 `docs/quickstart.md` 的内容，删除 `QUICK_START.md`

#### 合并 2: LORA 推荐系统设计
**目标文件**: `docs/design/lora-recommendation/README.md`
**合并来源**:
- `LORA_RECOMMENDATION_DESIGN.md` - 主设计文档
- `TECH_STACK_DECISION.md` - 技术选型（OpenAI）
- `TECH_STACK_DECISION_QWEN.md` - 技术选型（Qwen）
- `EMBEDDING_VS_LLM_COMPARISON.md` - 对比分析

**操作**: 创建综合文档，包含所有技术决策

#### 合并 3: 语义搜索文档
**目标文件**: `docs/design/search/semantic-search.md`
**合并来源**:
- `SEMANTIC_SEARCH_EXPLANATION.md` - 原理解释
- `SEARCH_MECHANISM_FAQ.md` - FAQ
- `NAME_WEIGHTING_SOLUTION.md` - 名称加权

**操作**: 整合为完整的搜索系统文档

#### 合并 4: LORA 元数据改进
**目标文件**: `docs/design/lora-recommendation/metadata-improvement.md`
**合并来源**:
- `LORA_METADATA_IMPROVEMENT_GUIDE.md` - 改进指南
- `SYNONYM_MAINTENANCE_GUIDE.md` - 维护指南
- `SYNONYM_CONFLICT_GUIDE.md` - 冲突处理
- `SYNONYM_SYSTEM_SUMMARY.md` - 系统总结
- `QUICK_REFERENCE.md` - 快速参考

**操作**: 创建统一的元数据改进文档

#### 合并 5: Story Mode 文档
**目标文件**: `docs/story-mode/README.md`
**合并来源**:
- `docs/story-mode.md` - 当前索引
- 其他 story-mode 文档保持独立

**操作**: 保持当前结构，只移动到新目录

### 第三步：删除文件清单

**可以安全删除的文件**:
```
PROMPT_OPTIMIZER_IMPROVEMENTS.md      # 已实现
DETAILED_EXPANSION_GUIDE.md           # 已实现
QUICK_START.md                         # 与 docs/quickstart.md 重复
```

**归档的文件**（移动到 docs/archive/）:
```
docs/5090_SYNC_REPORT.md               # 历史报告
OPTIMIZATION_RECOMMENDATIONS.md        # 已实施的优化建议
```

### 第四步：重命名文件

**统一命名规范**（使用小写和连字符）:
```
LORA_RECOMMENDATION_DESIGN.md → lora-recommendation-design.md
TECH_STACK_DECISION.md → tech-stack-decision.md
VIDEO_GENERATION_WORKFLOW_PLAN_V2.md → video-workflow-v2.md
```

---

## 执行计划

### Phase 1: 创建目录结构（5分钟）
```bash
mkdir -p docs/user-guide
mkdir -p docs/story-mode
mkdir -p docs/design/lora-recommendation
mkdir -p docs/design/video-workflow
mkdir -p docs/design/search
mkdir -p docs/testing
mkdir -p docs/archive
```

### Phase 2: 移动和重命名文件（10分钟）
```bash
# 移动用户文档
mv docs/quickstart.md docs/user-guide/
mv docs/workflow-guide.md docs/user-guide/
mv docs/advanced-workflow.md docs/user-guide/
mv docs/prompt-best-practices.md docs/user-guide/
mv docs/duration-guide.md docs/user-guide/
mv docs/video-on-demand.md docs/user-guide/
mv docs/query-tabs-guide.md docs/user-guide/
mv docs/civitai-length-guide.md docs/user-guide/

# 移动 Story Mode 文档
mv docs/story-mode*.md docs/story-mode/

# 移动设计文档
mv LORA_RECOMMENDATION_DESIGN.md docs/design/lora-recommendation/system-design.md
mv TECH_STACK_DECISION*.md docs/design/lora-recommendation/
mv EMBEDDING_VS_LLM_COMPARISON.md docs/design/lora-recommendation/
mv SEMANTIC_SEARCH_EXPLANATION.md docs/design/search/
mv SEARCH_MECHANISM_FAQ.md docs/design/search/
mv NAME_WEIGHTING_SOLUTION.md docs/design/search/
mv VIDEO_GENERATION_WORKFLOW_PLAN_V2.md docs/design/video-workflow/

# 移动测试文档
mv STAGE_TESTING_SPEC.md docs/testing/

# 归档
mv docs/5090_SYNC_REPORT.md docs/archive/
mv OPTIMIZATION_RECOMMENDATIONS.md docs/archive/
```

### Phase 3: 删除重复文件（2分钟）
```bash
rm PROMPT_OPTIMIZER_IMPROVEMENTS.md
rm DETAILED_EXPANSION_GUIDE.md
rm QUICK_START.md
```

### Phase 4: 创建索引文档（10分钟）

创建 `docs/README.md`:
```markdown
# Wan2.2 Video Service 文档

## 用户指南
- [快速开始](user-guide/quickstart.md)
- [Workflow 指南](user-guide/workflow-guide.md)
- [高级 Workflow](user-guide/advanced-workflow.md)
- [Prompt 最佳实践](user-guide/prompt-best-practices.md)

## Story Mode
- [Story Mode 概述](story-mode/README.md)
- [快速开始](story-mode/quickstart.md)
- [API 示例](story-mode/api-examples.md)
- [性能优化](story-mode/optimization.md)

## 技术设计
- [LORA 推荐系统](design/lora-recommendation/system-design.md)
- [语义搜索系统](design/search/semantic-search.md)
- [视频生成工作流](design/video-workflow/workflow-v2.md)

## 测试
- [阶段测试规范](testing/stage-testing.md)
```

### Phase 5: 更新 README.md 链接（5分钟）

更新主 `README.md` 中的文档链接，指向新的位置。

---

## 预期效果

### 整理前（42个文件）
```
项目根目录: 25个 .md 文件（混乱）
docs/: 17个 .md 文件（部分组织）
```

### 整理后（约30个文件）
```
项目根目录: 3个核心文件
docs/
├── user-guide/: 8个文件
├── story-mode/: 4个文件
├── design/: 12个文件
├── testing/: 1个文件
└── archive/: 2个文件
```

**改进**:
- ✅ 减少 12 个重复/过时文件
- ✅ 清晰的目录结构
- ✅ 统一的命名规范
- ✅ 易于查找和维护

---

## 注意事项

1. **备份**: 执行前先备份整个项目
2. **Git 提交**: 分阶段提交，便于回滚
3. **链接更新**: 检查所有内部链接是否正确
4. **CI/CD**: 检查是否有脚本依赖这些文档路径

---

## 执行时间估算

- Phase 1: 5分钟
- Phase 2: 10分钟
- Phase 3: 2分钟
- Phase 4: 10分钟
- Phase 5: 5分钟
- 验证和测试: 10分钟

**总计**: 约 40-50 分钟
