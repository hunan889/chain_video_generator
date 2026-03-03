# 视频按需加载功能

## 更新说明

为了提升性能和节省带宽，现在所有视频都改为**按需加载**模式。

## 改进内容

### 之前的问题
- 查询任务列表时，所有视频自动开始下载
- 多个视频同时加载导致页面卡顿
- 浪费带宽，特别是在查看历史记录时

### 现在的解决方案
- 视频默认显示为占位符按钮
- 点击"🎬 点击加载视频"按钮后才开始加载
- 每次只加载用户想看的视频

## 影响范围

所有视频显示位置都已更新：

1. **查询任务** - 单个任务查询结果
2. **任务历史** - 历史任务列表
3. **长视频生成** - 分段视频显示
4. **实时任务监控** - 正在生成的任务
5. **合并视频** - 合并后的视频预览
6. **Chain任务** - 完整视频和分段视频

## 使用方法

### 查看视频
1. 找到想看的任务
2. 点击"🎬 点击加载视频"按钮
3. 视频开始加载并自动播放

### 下载视频
- 无需加载视频，直接点击"下载视频"链接即可

## 技术实现

### 占位符HTML
```html
<div id="video-placeholder-{id}"
     style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:16px;text-align:center;cursor:pointer"
     onclick="loadVideo('{id}', '{video_url}')">
  <div style="color:#7c83ff;font-size:14px;margin-bottom:6px">🎬 点击加载视频</div>
  <div style="font-size:11px;color:#888">避免自动加载，节省带宽</div>
</div>
```

### JavaScript函数
```javascript
function loadVideo(id, url) {
  const placeholder = document.getElementById('video-placeholder-' + id);
  if (!placeholder) return;

  placeholder.innerHTML = '<div style="color:#7c83ff;font-size:13px">加载中...</div>';

  // Create video element
  const video = document.createElement('video');
  video.src = url;
  video.controls = true;
  video.loop = true;
  video.autoplay = true;
  video.style.maxWidth = '100%';
  video.style.maxHeight = '300px';
  video.style.borderRadius = '6px';

  // Replace placeholder with video
  placeholder.replaceWith(video);
}
```

## 性能对比

### 之前（自动加载）
- 打开历史记录页面：立即开始下载所有视频
- 10个视频 × 10MB = 100MB 立即下载
- 页面卡顿，滚动不流畅

### 现在（按需加载）
- 打开历史记录页面：只加载HTML，不下载视频
- 只下载用户点击的视频
- 页面流畅，响应快速

## 用户体验

### 优点
✅ 页面加载速度快
✅ 节省带宽（特别是移动网络）
✅ 减少服务器负载
✅ 可以快速浏览任务列表
✅ 只加载感兴趣的视频

### 注意事项
- 点击后需要等待视频加载（取决于网络速度）
- 视频加载时会显示"加载中..."提示
- 下载链接不受影响，可以直接下载

## 示例场景

### 场景1：查看历史记录
```
用户操作：
1. 点击"查询任务"Tab
2. 点击"加载历史"按钮
3. 看到100个历史任务，每个都有占位符
4. 只点击感兴趣的3个视频查看
5. 只下载了3个视频，节省了97个视频的带宽
```

### 场景2：长视频生成
```
用户操作：
1. 生成5段视频
2. 每段完成后显示占位符
3. 只点击查看第1段和最后1段
4. 节省了3段视频的带宽
```

## 兼容性

- 所有现代浏览器都支持
- 移动端友好
- 不影响下载功能

## 未来改进

可能的增强功能：
- 添加视频缩略图预览
- 支持视频预加载（鼠标悬停时）
- 记住用户已加载的视频
- 批量加载选项

## 总结

这个改进显著提升了系统性能，特别是在查看大量历史任务时。用户现在可以快速浏览任务列表，只加载需要查看的视频，大大节省了带宽和时间。
