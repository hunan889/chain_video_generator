# Tests

测试脚本集合，用于验证各个功能模块。

## 测试文件

### Prompt 优化测试
- `test_prompt_optimizer.py` - Prompt 优化器功能测试
- `test_detailed_expansion.py` - 详细扩展对比测试
- `test_api_optimize.py` - Prompt 优化 API 端点测试

### 生成功能测试
- `test_t2v_generation.py` - T2V 生成端点测试
- `test_lora_injection.py` - LoRA 注入功能测试

### LoRA 推荐测试
- `test_lora_recommend.py` - LoRA 自动推荐测试
- `test_embedding_service.py` - Embedding 服务测试
- `test_embedding_init.py` - Embedding 初始化测试

### 搜索功能测试
- `test_search.py` - 基础搜索功能测试
- `test_search_on_all_fours.py` - 特定搜索场景测试
- `test_name_weighting.py` - 名称加权测试

### Workflow 测试
- `workflow_test.py` - Advanced Workflow 分阶段测试端点

## 运行测试

```bash
# 运行单个测试
python tests/test_prompt_optimizer.py

# 运行 API 测试（需要服务运行）
python tests/test_api_optimize.py
python tests/test_t2v_generation.py
```

## 注意事项

- 大部分测试需要服务运行在 `http://127.0.0.1:8000`
- API 测试需要有效的 API Key（默认：`wan22-default-key-change-me`）
- Embedding 相关测试需要模型文件已下载
