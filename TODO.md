# LORA推荐系统实施任务清单

## ✅ 已完成

- [x] 技术方案设计和讨论
- [x] 技术选型决策（BGE-Large-ZH）
- [x] 下载并部署Embedding模型
- [x] 实现EmbeddingService核心服务
- [x] 集成Zilliz向量数据库
- [x] 编写测试脚本并验证
- [x] 配置环境变量
- [x] 编写完整文档

---

## 📋 待办事项

### Phase 1: 数据准备 (预计1-2天)

#### 1.1 LORA元数据同步
- [ ] 创建数据库迁移脚本
  - [ ] 检查 `lora_metadata` 表结构
  - [ ] 编写 `scripts/sync_loras_to_db.py`
  - [ ] 从 `config/loras.yaml` 读取数据
  - [ ] 插入到 `lora_metadata` 表
  - [ ] 处理重复数据（更新 vs 跳过）

#### 1.2 LORA分类标注
- [ ] 实现LLM辅助分类
  - [ ] 创建 `api/services/lora_classifier.py`
  - [ ] 使用Qwen3-14B分析LORA信息
  - [ ] 建议分类（action/body/scene/style/position/modifier）
  - [ ] 返回置信度和理由
- [ ] 创建分类管理脚本
  - [ ] `scripts/classify_loras.py`
  - [ ] 批量处理所有LORA
  - [ ] 生成分类建议CSV
  - [ ] 人工审核和确认

#### 1.3 建立初始索引
- [ ] 为收藏资源建立索引
  - [ ] 查询所有收藏的资源（favorites表）
  - [ ] 批量生成embedding
  - [ ] 插入到Zilliz
  - [ ] 记录进度和错误
- [ ] 为收藏LORA建立索引
  - [ ] 查询所有收藏的LORA（lora_favorites表）
  - [ ] 提取example_prompts
  - [ ] 批量生成embedding
  - [ ] 插入到Zilliz

---

### Phase 2: 后端API开发 (预计2-3天)

#### 2.1 索引管理API
- [ ] `POST /api/v1/admin/embeddings/rebuild-favorites`
  - [ ] 创建异步任务
  - [ ] 批量处理收藏资源
  - [ ] 返回任务ID
- [ ] `POST /api/v1/admin/embeddings/rebuild-loras`
  - [ ] 创建异步任务
  - [ ] 批量处理收藏LORA
  - [ ] 返回任务ID
- [ ] `GET /api/v1/admin/embeddings/stats`
  - [ ] 返回索引统计信息
  - [ ] 总数、资源数、LORA数
- [ ] `GET /api/v1/admin/embeddings/tasks/{task_id}`
  - [ ] 查询任务状态
  - [ ] 返回进度百分比
- [ ] `DELETE /api/v1/admin/embeddings/clear-all`
  - [ ] 清空所有索引
  - [ ] 需要确认

#### 2.2 语义搜索API
- [ ] `POST /api/v1/search/resources`
  - [ ] 接收query和top_k
  - [ ] 调用embedding_service.search_similar_resources
  - [ ] 从数据库获取完整资源信息
  - [ ] 返回资源列表（含相似度）
- [ ] `POST /api/v1/search/loras`
  - [ ] 接收query、mode、top_k
  - [ ] 调用embedding_service.search_similar_loras
  - [ ] 从数据库获取完整LORA信息
  - [ ] 返回LORA列表（含相似度、触发词）

#### 2.3 智能推荐API
- [ ] `POST /api/v1/recommend`
  - [ ] 接收prompt和选项（include_images/loras）
  - [ ] 并行调用：
    - [ ] 语义搜索相似图片
    - [ ] 语义搜索相似LORA
    - [ ] LLM推荐LORA（现有功能）
  - [ ] 合并和去重结果
  - [ ] 返回综合推荐

#### 2.4 LORA管理API
- [ ] `POST /api/v1/admin/loras/{id}/suggest-category`
  - [ ] 调用lora_classifier
  - [ ] 返回分类建议和理由
- [ ] `PATCH /api/v1/admin/loras/{id}`
  - [ ] 更新LORA元数据
  - [ ] 支持：category、quality_score、custom_tags
- [ ] `POST /api/v1/admin/loras/batch-suggest`
  - [ ] 批量分类建议
  - [ ] 返回所有LORA的建议

---

### Phase 3: 前端开发 (预计2-3天)

#### 3.1 索引管理页面
- [ ] 创建新Tab：系统管理 → 索引管理
- [ ] 显示索引统计
  - [ ] 总索引数
  - [ ] 资源索引数 / 收藏数
  - [ ] LORA索引数 / 收藏数
  - [ ] 模型信息
  - [ ] 最后更新时间
- [ ] 操作按钮
  - [ ] 重建收藏资源索引
  - [ ] 重建收藏LORA索引
  - [ ] 清空所有索引
- [ ] 任务列表
  - [ ] 显示正在运行的任务
  - [ ] 进度条
  - [ ] 取消按钮

#### 3.2 LORA分类管理页面
- [ ] 创建新Tab：系统管理 → LORA分类
- [ ] LORA列表
  - [ ] 支持筛选（未分类/全部/按分类）
  - [ ] 显示：名称、描述、标签、当前分类
- [ ] 单个LORA编辑
  - [ ] 显示AI分类建议（置信度、理由）
  - [ ] 分类下拉选择
  - [ ] 质量评分（1-10星）
  - [ ] 保存按钮
  - [ ] 建立索引按钮
- [ ] 批量操作
  - [ ] 批量建议分类按钮
  - [ ] 批量应用分类

#### 3.3 智能推荐面板
- [ ] 在T2V/I2V页面添加"智能推荐"按钮
- [ ] 弹出推荐面板
  - [ ] 相似图片区域
    - [ ] 显示3-5张相似图片
    - [ ] 显示相似度
    - [ ] 点击使用按钮
  - [ ] 推荐图片LORA区域
    - [ ] 显示推荐的图片LORA
    - [ ] 显示触发词
    - [ ] 复选框选择
  - [ ] 推荐视频LORA区域
    - [ ] 显示推荐的视频LORA
    - [ ] 显示触发词和相似度
    - [ ] 复选框选择
  - [ ] 优化后的Prompt显示
  - [ ] 应用按钮（应用选中的LORA和图片）

---

### Phase 4: 测试与优化 (预计1-2天)

#### 4.1 功能测试
- [ ] 端到端测试
  - [ ] 索引构建流程
  - [ ] 语义搜索准确性
  - [ ] 推荐结果质量
- [ ] 边界情况测试
  - [ ] 空查询
  - [ ] 超长prompt
  - [ ] 无匹配结果
  - [ ] 并发请求

#### 4.2 性能测试
- [ ] 索引构建速度
  - [ ] 1000个资源需要多久
  - [ ] 100个LORA需要多久
- [ ] 查询响应时间
  - [ ] 单次查询延迟
  - [ ] 并发查询性能
- [ ] 内存占用
  - [ ] Embedding服务内存
  - [ ] Zilliz内存

#### 4.3 用户体验优化
- [ ] 加载状态提示
- [ ] 错误处理和提示
- [ ] 响应速度优化
- [ ] UI/UX改进

---

## 📊 进度追踪

| Phase | 任务数 | 已完成 | 进度 |
|-------|--------|--------|------|
| Phase 0 (部署) | 8 | 8 | 100% ✅ |
| Phase 1 (数据) | 6 | 0 | 0% |
| Phase 2 (API) | 12 | 0 | 0% |
| Phase 3 (前端) | 9 | 0 | 0% |
| Phase 4 (测试) | 9 | 0 | 0% |
| **总计** | **44** | **8** | **18%** |

---

## 🎯 里程碑

- [x] **M0**: Embedding服务部署完成 (2026-03-12) ✅
- [ ] **M1**: 数据准备完成，初始索引建立
- [ ] **M2**: 后端API开发完成，可通过API搜索
- [ ] **M3**: 前端集成完成，用户可使用智能推荐
- [ ] **M4**: 测试完成，系统上线

---

## 📝 注意事项

1. **数据库表**: 确保 `lora_metadata`、`lora_favorites`、`prompt_embeddings` 表已创建
2. **GPU资源**: 建议使用GPU 3运行Embedding服务
3. **批量处理**: 索引构建使用批量API，提高效率
4. **错误处理**: 记录失败的索引，支持重试
5. **进度反馈**: 长时间任务提供进度更新

---

## 🚀 快速开始下一阶段

### 开始Phase 1
```bash
# 1. 同步LORA元数据
python scripts/sync_loras_to_db.py

# 2. 分类LORA
python scripts/classify_loras.py

# 3. 建立初始索引
python scripts/build_initial_index.py
```

---

**当前状态**: Phase 0 完成，准备开始 Phase 1
**下一步**: 同步LORA元数据到数据库
