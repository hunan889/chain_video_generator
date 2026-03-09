# 使用 Claude Code 开发本项目

本项目使用 Claude Code 进行开发和维护。

## 什么是 Claude Code？

Claude Code 是 Anthropic 提供的 AI 编程助手，可以帮助你：
- 理解和修改代码
- 添加新功能
- 调试问题
- 优化性能
- 编写文档

## 安装 Claude Code

### 方法 1：使用 npm（推荐）

```bash
npm install -g @anthropic-ai/claude-code
```

### 方法 2：使用 curl

```bash
curl -fsSL https://claude.ai/install.sh | sh
```

## 在本项目中使用 Claude

### 1. 进入项目目录

```bash
cd /path/to/chain_video_generator
```

### 2. 启动 Claude Code

```bash
claude
```

### 3. 常用命令

在 Claude Code 中，你可以：

```bash
# 查看帮助
/help

# 查看项目文件
/glob **/*.py

# 搜索代码
/grep "story_mode"

# 读取文件
/read api/services/task_manager.py

# 提交代码
/commit

# 创建 PR
/pr
```

## 项目知识库

`.claude/memory/` 目录包含项目的知识积累：
- 项目架构和设计决策
- 性能优化经验
- 常见问题解决方案
- 开发过程中的发现

Claude Code 会自动读取这些文件，更好地理解项目上下文。

## 常见任务示例

### 添加新功能

```
你：帮我添加一个新的 API 端点，用于查询所有任务的统计信息

Claude 会：
1. 分析现有代码结构
2. 创建新的路由文件
3. 实现业务逻辑
4. 更新文档
5. 提交代码
```

### 调试问题

```
你：Story 模式生成失败，帮我排查原因

Claude 会：
1. 查看日志文件
2. 分析错误信息
3. 检查相关代码
4. 提供解决方案
5. 修复问题
```

### 性能优化

```
你：帮我优化视频生成的性能

Claude 会：
1. 分析性能瓶颈
2. 查看 ComfyUI 日志
3. 提出优化建议
4. 实现优化方案
5. 测试验证
```

## 项目特定提示

在使用 Claude Code 开发本项目时，可以参考：

1. **Story 模式**：查看 `STORY_UPDATES.md`
2. **Workflow 构建**：查看 `api/services/workflow_builder.py`
3. **任务管理**：查看 `api/services/task_manager.py`
4. **性能数据**：查看 README.md 中的性能对比

## 最佳实践

1. **明确需求**：清楚地描述你想要实现的功能
2. **提供上下文**：告诉 Claude 相关的文件和功能
3. **逐步验证**：每次修改后测试功能是否正常
4. **保持沟通**：如果结果不符合预期，及时反馈

## 注意事项

- `.claude/` 目录不是可执行文件，而是项目配置
- Claude Code 需要联网才能工作
- 敏感信息（API keys, tokens）不会被发送到服务器
- 所有代码修改都需要你的确认

## 获取帮助

- Claude Code 文档：https://docs.anthropic.com/claude-code
- 项目文档：README.md
- 快速开始：QUICKSTART.md

## 示例会话

```
你：帮我分析 Story 模式的性能瓶颈

Claude：让我查看相关代码和日志...
[分析 task_manager.py, workflow_builder.py, ComfyUI 日志]

发现以下性能瓶颈：
1. 模型加载时间：已优化（共享加载）
2. 采样速度：10-20秒/step（主要瓶颈）
3. 视频编码：每段 5-15 秒

建议优化：
1. 降低 steps（从 20 到 10-15）
2. 使用 Merged Workflow（已实现）
3. 调整 upscale batch size

需要我实现这些优化吗？
```

## 总结

Claude Code 是一个强大的 AI 编程助手，可以大大提高开发效率。
在本项目中，它已经帮助实现了 Story 模式、性能优化、文档编写等功能。

祝您开发愉快！🚀
