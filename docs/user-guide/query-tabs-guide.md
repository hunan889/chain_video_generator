# 查询任务 - 子Tab分类功能

## 更新说明

在"查询任务"Tab中添加了子Tab，可以按任务类型过滤显示历史记录。

## 功能特点

### 子Tab分类

现在可以按以下类型过滤任务：

1. **全部** - 显示所有任务（不包括Story任务）
2. **T2V** - 只显示文字生成视频任务
3. **I2V** - 只显示图片生成视频任务
4. **长视频** - 只显示Chain任务（多段拼接）
5. **Story** - 只显示Story workflow任务
6. **延续** - 只显示Extend任务（视频延续）

### Story任务识别

Story任务的特征：
- 模式为T2V
- 包含`workflow_name`参数
- 通过`/api/v1/workflow/run`接口创建
- 显示为紫色的"STORY"标签

## 使用方法

### 查看所有任务
1. 点击"查询任务"Tab
2. 点击"刷新历史"按钮
3. 默认显示"全部"任务

### 过滤特定类型
1. 点击对应的子Tab按钮
2. 列表自动过滤显示该类型的任务

### 查看Story任务
1. 点击"Story"子Tab
2. 只显示通过workflow运行的任务
3. 任务卡片会显示workflow名称

## 界面示例

```
┌─────────────────────────────────────────┐
│ 查询任务                                 │
├─────────────────────────────────────────┤
│ 任务ID: [________] [查询] [刷新历史]    │
├─────────────────────────────────────────┤
│ 生成历史                                 │
│ [全部] [T2V] [I2V] [长视频] [Story] [延续] │
├─────────────────────────────────────────┤
│ ┌─────────────────────────────────────┐ │
│ │ STORY  WAN2.2-I2V-AutoPrompt-Story  │ │
│ │ Workflow: WAN2.2-I2V-AutoPrompt...  │ │
│ │ 🎬 点击加载视频                      │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

## 任务类型标识

### 普通任务
- **T2V** - 蓝色标签
- **I2V** - 蓝色标签
- **EXTEND** - 蓝色标签

### 特殊任务
- **STORY** - 紫色标签（#9d4edd）
- **长视频** - 紫色边框，显示段数

## 过滤逻辑

### Story任务判断
```javascript
const isStory = t.params && t.params.workflow_name;
```

### 过滤规则
- **全部**：显示所有任务，但排除Story任务
- **Story**：只显示有workflow_name的T2V任务
- **T2V**：显示T2V任务，但排除Story任务
- **I2V**：只显示I2V任务
- **长视频**：只显示Chain任务
- **延续**：只显示Extend任务

## 技术实现

### 全局变量
```javascript
let allHistoryTasks = [];      // 所有任务
let allHistoryChains = [];     // 所有Chain
let currentHistoryFilter = 'all';  // 当前过滤器
```

### 过滤函数
```javascript
function filterHistory(type, btn) {
  currentHistoryFilter = type;

  // 更新按钮状态
  document.querySelectorAll('.query-subtab').forEach(b =>
    b.classList.remove('active')
  );
  btn.classList.add('active');

  // 重新渲染
  renderHistory();
}
```

### 渲染函数
```javascript
function renderHistory() {
  // 根据currentHistoryFilter过滤任务
  let filteredTasks = allHistoryTasks.filter(t => {
    if (currentHistoryFilter === 'story') {
      return t.mode === 't2v' && t.params && t.params.workflow_name;
    }
    return t.mode === currentHistoryFilter;
  });

  // 渲染过滤后的任务
  // ...
}
```

## 样式

### 子Tab按钮
```css
.query-subtab {
  background: #16213e;
  border: 1px solid #444;
  padding: 6px 16px;
  font-size: 12px;
}

.query-subtab.active {
  background: #0f3460;
  border-color: #7c83ff;
  color: #7c83ff;
}

.query-subtab:hover {
  background: #1a3a4a;
}
```

### Story任务标签
```html
<b style="color:#9d4edd">STORY</b>
```

## 数据流

```
用户点击"刷新历史"
    ↓
loadHistory() - 从API获取数据
    ↓
存储到全局变量
    ↓
renderHistory() - 根据过滤器渲染
    ↓
显示过滤后的任务列表
```

## 优势

1. **清晰分类** - 不同类型的任务分开显示
2. **快速查找** - 点击子Tab即可过滤
3. **Story独立** - Story任务有专门的Tab
4. **保持性能** - 只渲染需要显示的任务
5. **用户友好** - 直观的界面，易于使用

## 示例场景

### 场景1：查看Story任务
```
1. 用户在Story Tab生成了视频
2. 点击"查看任务"按钮
3. 自动跳转到查询任务Tab
4. 点击"Story"子Tab
5. 看到所有Story任务
```

### 场景2：查看长视频
```
1. 用户生成了多个长视频
2. 点击"长视频"子Tab
3. 只显示Chain任务
4. 可以查看每段的详细信息
```

## 注意事项

- Story任务不会出现在"全部"Tab中，避免混淆
- 每次刷新历史后，过滤器保持当前选择
- 子Tab状态会高亮显示当前选择

## 未来改进

可能的增强功能：
- 添加搜索功能
- 按时间范围过滤
- 按状态过滤（完成/失败）
- 导出任务列表
- 批量操作

## 总结

子Tab分类功能让用户可以快速找到特定类型的任务，特别是新增的Story任务类型。界面更加清晰，使用更加方便。
