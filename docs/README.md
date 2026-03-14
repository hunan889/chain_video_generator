# Wan2.2 Video Service 文档

## 用户指南

- [快速开始](user-guide/quickstart.md) - 一键安装和快速测试
- [Workflow 指南](user-guide/workflow-guide.md) - Workflow 基础使用
- [高级 Workflow](user-guide/advanced-workflow.md) - 高级功能和自定义
- [Prompt 最佳实践](user-guide/prompt-best-practices.md) - 提示词编写技巧
- [时长计算指南](user-guide/duration-guide.md) - 视频时长和帧数计算
- [视频点播](user-guide/video-on-demand.md) - 视频点播功能
- [查询标签指南](user-guide/query-tabs-guide.md) - 查询标签使用
- [Civitai 长度指南](user-guide/civitai-length-guide.md) - Civitai 相关说明

## Story Mode

- [Story Mode 概述](story-mode/README.md) - Story Mode 功能介绍
- [快速开始](story-mode/quickstart.md) - Story Mode 快速上手
- [API 示例](story-mode/api-examples.md) - API 调用示例
- [性能优化](story-mode/optimization.md) - 性能优化建议

## 技术设计

### LORA 推荐系统
- [系统设计](design/lora-recommendation/system-design.md) - 整体架构设计
- [技术选型 (OpenAI)](design/lora-recommendation/tech-stack-openai.md) - 使用 OpenAI Embedding
- [技术选型 (Qwen)](design/lora-recommendation/tech-stack-qwen.md) - 使用 Qwen 本地模型
- [Embedding vs LLM](design/lora-recommendation/embedding-vs-llm.md) - 技术方案对比
- [元数据改进](design/lora-recommendation/metadata-improvement.md) - LORA 元数据优化
- [同义词维护](design/lora-recommendation/synonym-maintenance.md) - 同义词系统维护
- [同义词冲突](design/lora-recommendation/synonym-conflict.md) - 冲突处理方案
- [系统总结](design/lora-recommendation/synonym-summary.md) - 同义词系统总结

### 搜索系统
- [语义搜索](design/search/semantic-search.md) - 语义搜索原理
- [搜索机制 FAQ](design/search/mechanism-faq.md) - 常见问题解答
- [名称加权](design/search/name-weighting.md) - 名称加权方案
- [关键词设计](design/search/keywords-design.md) - 搜索关键词设计

### 视频生成工作流
- [工作流 V2](design/video-workflow/workflow-v2.md) - 视频生成工作流设计
- [参数重设计](design/video-workflow/params-redesign.md) - Workflow 参数设计

## 测试

- [阶段测试规范](testing/stage-testing.md) - 测试规范和流程

## 归档

- [5090 同步报告](archive/5090_SYNC_REPORT.md) - 历史同步报告
- [优化建议](archive/optimization-recommendations.md) - 已实施的优化建议

---

## 快速导航

### 我是新用户
1. 阅读 [快速开始](user-guide/quickstart.md)
2. 了解 [Prompt 最佳实践](user-guide/prompt-best-practices.md)
3. 尝试 [Story Mode](story-mode/quickstart.md)

### 我是开发者
1. 查看 [系统设计](design/lora-recommendation/system-design.md)
2. 了解 [技术选型](design/lora-recommendation/tech-stack-qwen.md)
3. 参考 [API 示例](story-mode/api-examples.md)

### 我想优化性能
1. 阅读 [Story Mode 优化](story-mode/optimization.md)
2. 查看 [搜索系统设计](design/search/semantic-search.md)
3. 参考 [元数据改进](design/lora-recommendation/metadata-improvement.md)

---

**最后更新**: 2026-03-14
