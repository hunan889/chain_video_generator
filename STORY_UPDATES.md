# Story视频功能更新总结

## 1. ✅ 已完成：保存上次选择

### 添加的功能
Story视频现在会自动保存和恢复上次的参数设置，包括：

- Workflow选择
- Prompt内容
- 模型选择（a14b/5b）
- 宽度、高度
- 时长（秒）
- FPS
- Steps、CFG、Shift
- Seed
- 自定义参数（JSON）

### 实现方式

#### 1. 添加到FORM_FIELDS配置
```javascript
const FORM_FIELDS = {
  // ...
  story: ['story-workflow','story-prompt','story-model','story-w','story-h',
          'story-duration','story-fps','story-steps','story-cfg','story-shift',
          'story-seed','story-custom-params']
};
```

#### 2. 提交时保存
```javascript
async function submitStoryWorkflow() {
  // 验证workflow
  if (!workflowName) {
    alert('请选择一个 Workflow');
    return;
  }

  // 保存表单参数
  saveFormParams('story');

  // 继续提交...
}
```

#### 3. Tab激活时恢复
```javascript
storyTab.addEventListener('click', () => {
  loadWorkflowList();
  restoreFormParams('story');  // 恢复保存的参数
});
```

### 使用体验

**之前**：
- 每次切换Tab都要重新输入所有参数
- 容易忘记上次使用的设置

**现在**：
- 自动记住上次的所有设置
- 切换Tab后参数自动恢复
- 提高工作效率

### 存储位置
参数保存在浏览器的localStorage中：
- Key: `wan22_story`
- 数据格式: JSON

### 示例
```javascript
{
  "story-workflow": "WAN2.2-I2V-AutoPrompt-Story",
  "story-prompt": "A woman dancing",
  "story-model": "a14b",
  "story-w": "832",
  "story-h": "480",
  "story-duration": "3.3",
  "story-fps": "24",
  "story-steps": "20",
  "story-cfg": "6.0",
  "story-shift": "5.0",
  "story-seed": "-1",
  "story-custom-params": ""
}
```

---

## 2. ⚠️ ComfyUI 500错误排查

### 错误信息
```
ComfyUI prompt failed (500): 500 Internal Server Error
Server got itself in trouble
```

### 可能的原因

#### 1. Workflow JSON问题
- Workflow文件格式不正确
- 参数占位符未正确替换
- 节点连接错误

#### 2. 缺少必需节点
CivitAI workflow可能使用了自定义节点，需要安装：
```bash
cd /home/gime/soft/ComfyUI/custom_nodes
git clone https://github.com/rgthree/rgthree-comfy
git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
```

#### 3. 模型文件缺失
检查是否下载了所需的模型：
```bash
ls -lh /home/gime/soft/ComfyUI/models/checkpoints/
ls -lh /home/gime/soft/ComfyUI/models/vae/
ls -lh /home/gime/soft/ComfyUI/models/loras/
```

#### 4. 参数值不正确
- 帧数不符合4n+1格式
- 分辨率超出限制
- LoRA文件不存在

### 排查步骤

#### 步骤1：检查ComfyUI日志
```bash
# 查看ComfyUI进程
ps aux | grep ComfyUI

# 如果使用screen运行
screen -r comfyui-a14b

# 查看最近的错误
```

#### 步骤2：验证workflow
```bash
# 检查workflow文件
cat /home/gime/soft/wan22-service/workflows/WAN2.2-I2V-AutoPrompt-Story.json | jq . | head -50

# 检查文件大小
ls -lh /home/gime/soft/wan22-service/workflows/WAN2.2-I2V-AutoPrompt-Story.json
```

#### 步骤3：测试简单workflow
先用简单的workflow测试，确认基本功能正常：
```bash
curl -X POST http://localhost:8000/api/v1/workflow/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wan22-default-key-change-me" \
  -d '{
    "workflow_name": "t2v_a14b",
    "model": "a14b",
    "params": {
      "prompt": "test",
      "num_frames": 81,
      "width": 832,
      "height": 480
    }
  }'
```

#### 步骤4：检查参数替换
API会尝试替换workflow中的参数，检查是否正确：
- 简单占位符：`${param_name}`
- 节点标题匹配：`Lenght`, `Steps`, `WIDTH`, `HEIGHT`

### 解决方案

#### 方案1：使用简单workflow
如果CivitAI workflow太复杂，可以使用系统自带的workflow：
- `t2v_a14b.json` - T2V生成
- `i2v_a14b.json` - I2V生成

#### 方案2：修复CivitAI workflow
1. 检查workflow中使用的节点
2. 安装缺失的自定义节点
3. 下载缺失的模型文件

#### 方案3：简化参数
使用最基本的参数测试：
```json
{
  "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
  "model": "a14b",
  "params": {
    "prompt": "A woman walking",
    "num_frames": 81,
    "width": 832,
    "height": 480,
    "fps": 24,
    "steps": 20
  }
}
```

### 调试技巧

#### 1. 查看API日志
```bash
tail -f /tmp/wan22-api.log
```

#### 2. 查看ComfyUI日志
ComfyUI的终端输出会显示详细的错误信息

#### 3. 使用ComfyUI Web界面
访问 http://localhost:8188 直接在ComfyUI中测试workflow

#### 4. 检查workflow参数
```python
import json

# 读取workflow
with open('workflows/WAN2.2-I2V-AutoPrompt-Story.json') as f:
    wf = json.load(f)

# 检查节点
for node in wf['nodes']:
    if node['type'] == 'mxSlider':
        print(f"Slider: {node['title']} = {node['widgets_values']}")
```

### 常见问题

#### Q: 为什么会出现500错误？
A: ComfyUI内部处理workflow时出错，通常是workflow格式或参数问题。

#### Q: 如何知道缺少哪些节点？
A: 查看ComfyUI日志，会显示"Unknown node type"错误。

#### Q: 参数替换不生效怎么办？
A: 检查workflow JSON，确认占位符格式正确，或使用节点标题匹配。

#### Q: 如何测试workflow是否正确？
A: 在ComfyUI Web界面中手动加载workflow并运行。

---

## 总结

### 已完成
✅ Story视频保存上次选择功能
✅ 自动恢复参数
✅ 提升用户体验

### 待解决
⚠️ ComfyUI 500错误需要进一步排查
- 检查ComfyUI日志
- 验证workflow文件
- 确认所需节点已安装
- 测试简单workflow

### 建议
1. 先用系统自带的workflow测试基本功能
2. 逐步添加参数，找出导致错误的具体参数
3. 查看ComfyUI日志获取详细错误信息
4. 如果CivitAI workflow太复杂，考虑使用简化版本
