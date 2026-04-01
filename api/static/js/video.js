// Sub-tab switching (within video section)
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel:not(#panel-image)').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    const panelId = 'panel-' + t.dataset.tab;
    const panel = document.getElementById(panelId);
    if (panel) {
      panel.classList.add('active');
    }
    localStorage.setItem('wan22_active_tab', t.dataset.tab);
    if (t.dataset.tab === 'dlora') refreshLoraFiles();
    if (t.dataset.tab === 'query') loadHistory();
    if (t.dataset.tab === 'civitai' && !document.getElementById('civitai-results').innerHTML) searchCivitAI();
  });
});

// Restore last active sub-tab (without triggering side-effect requests)
try {
  const savedTab = localStorage.getItem('wan22_active_tab');
  if (savedTab) {
    const tabEl = document.querySelector(`#section-video .tab[data-tab="${savedTab}"]`);
    if (tabEl) {
      document.querySelectorAll('#section-video .tab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('#section-video .panel').forEach(x => x.classList.remove('active'));
      tabEl.classList.add('active');
      const panel = document.getElementById('panel-' + savedTab);
      if (panel) panel.classList.add('active');
    }
  }
} catch(e) {}

async function submitT2V() {
  const seed = parseInt(document.getElementById('t2v-seed').value);
  const fps = parseInt(document.getElementById('t2v-fps').value);
  const duration = parseFloat(document.getElementById('t2v-duration').value);
  let prompt = document.getElementById('t2v-prompt').value;
  if (!prompt) { alert('请输入提示词'); return; }
  const loras = getSelectedLoras('t2v-loras');

  // Check face swap
  const faceSwapEnabled = document.getElementById('t2v-faceswap').checked;
  const faceFile = document.getElementById('t2v-face-file').files[0];
  if (faceSwapEnabled && !faceFile) {
    alert('请上传目标人脸照片');
    return;
  }

  const body = {
    prompt,
    negative_prompt: document.getElementById('t2v-neg').value,
    model: document.getElementById('t2v-model').value,
    width: parseInt(document.getElementById('t2v-w').value),
    height: parseInt(document.getElementById('t2v-h').value),
    num_frames: durationToFrames(duration, fps),
    fps: fps,
    steps: parseInt(document.getElementById('t2v-steps').value),
    cfg: parseFloat(document.getElementById('t2v-cfg').value),
    shift: parseFloat(document.getElementById('t2v-shift').value),
    seed: seed >= 0 ? seed : null,
    scheduler: document.getElementById('t2v-sched').value,
    model_preset: document.getElementById('t2v-preset').value,
    t5_preset: document.getElementById('t2v-t5preset').value,
    loras: loras,
    upscale: document.getElementById('t2v-upscale').checked,
    auto_lora: document.getElementById('t2v-auto-lora').checked,
    auto_prompt: document.getElementById('t2v-auto-prompt').checked
  };

  // Add face swap parameters
  if (faceSwapEnabled) {
    body.face_swap = {
      enabled: true,
      strength: parseFloat(document.getElementById('t2v-face-strength').value)
    };
  }

  if (!body.prompt) { alert('请输入提示词'); return; }
  saveFormParams('t2v');

  try {
    // If face swap enabled, use FormData to upload face image
    let response;
    if (faceSwapEnabled && faceFile) {
      const formData = new FormData();
      formData.append('face_image', faceFile);
      formData.append('params', JSON.stringify(body));

      response = await fetch(BASE + '/api/v1/generate', {
        method: 'POST',
        headers: {'X-API-Key': getKey()},
        body: formData
      });
    } else {
      response = await fetch(BASE + '/api/v1/generate', {
        method: 'POST',
        headers: {'Content-Type':'application/json','X-API-Key':getKey()},
        body: JSON.stringify(body)
      });
    }

    const d = await response.json();
    if (!response.ok) { alert('Error: ' + (d.detail || JSON.stringify(d))); return; }
    addTask(d.task_id, 'T2V', body.prompt);
  } catch(e) { alert('请求失败: ' + e.message); }
}

async function submitI2V() {
  if (!i2vSelectedFile) { alert('请上传图片'); return; }
  let prompt = document.getElementById('i2v-prompt').value;
  if (!prompt) { alert('请输入提示词'); return; }
  const seed = parseInt(document.getElementById('i2v-seed').value);
  const fps = parseInt(document.getElementById('i2v-fps').value);
  const duration = parseFloat(document.getElementById('i2v-duration').value);
  const loras = getSelectedLoras('i2v-loras');

  // Check face swap
  const faceSwapEnabled = document.getElementById('i2v-faceswap').checked;
  const faceFile = document.getElementById('i2v-face-file').files[0];
  if (faceSwapEnabled && !faceFile) {
    alert('请上传目标人脸照片');
    return;
  }

  const params = {
    prompt, negative_prompt: document.getElementById('i2v-neg').value,
    model: document.getElementById('i2v-model').value,
    width: parseInt(document.getElementById('i2v-w').value),
    height: parseInt(document.getElementById('i2v-h').value),
    num_frames: durationToFrames(duration, fps),
    fps: fps,
    steps: parseInt(document.getElementById('i2v-steps').value),
    cfg: parseFloat(document.getElementById('i2v-cfg').value),
    shift: parseFloat(document.getElementById('i2v-shift').value),
    seed: seed >= 0 ? seed : null,
    scheduler: document.getElementById('i2v-sched').value,
    noise_aug_strength: parseFloat(document.getElementById('i2v-noise').value),
    motion_amplitude: parseFloat(document.getElementById('i2v-motion').value),
    color_match: document.getElementById('i2v-colormatch').value === 'true',
    color_match_method: document.getElementById('i2v-colormatch-method').value,
    resize_mode: document.getElementById('i2v-resize').value,
    model_preset: document.getElementById('i2v-preset').value,
    t5_preset: document.getElementById('i2v-t5preset').value,
    loras: loras,
    upscale: document.getElementById('i2v-upscale').checked,
    auto_lora: document.getElementById('i2v-auto-lora').checked,
    auto_prompt: document.getElementById('i2v-auto-prompt').checked
  };

  // Add face swap parameters
  if (faceSwapEnabled) {
    params.face_swap = {
      enabled: true,
      strength: parseFloat(document.getElementById('i2v-face-strength').value)
    };
  }

  saveFormParams('i2v');
  const fd = new FormData();
  fd.append('image', i2vSelectedFile);
  if (faceSwapEnabled && faceFile) {
    fd.append('face_image', faceFile);
  }
  fd.append('params', JSON.stringify(params));
  try {
    const r = await fetch(BASE + '/api/v1/generate/i2v', {
      method: 'POST', headers: {'X-API-Key': getKey()}, body: fd
    });
    const d = await r.json();
    if (!r.ok) { alert('Error: ' + (d.detail || JSON.stringify(d))); return; }
    addTask(d.task_id, 'I2V', prompt);
  } catch(e) { alert('请求失败: ' + e.message); }
}

// Task query
async function queryTask() {
  const tid = document.getElementById('query-id').value.trim();
  if (!tid) { alert('请输入任务ID'); return; }
  const box = document.getElementById('query-result');
  box.innerHTML = '<span style="color:#aaa">查询中...</span>';
  try {
    const r = await fetch(BASE + '/api/v1/tasks/' + tid, {headers:{'X-API-Key':getKey()}});
    const d = await r.json();
    if (!r.ok) { box.innerHTML = '<span style="color:#f87171">未找到任务</span>'; return; }
    const pct = Math.round((d.progress || 0) * 100);
    const statusText = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败'}[d.status] || d.status;
    let html = `<div class="task-card"><div class="task-header"><span class="task-id">${d.task_id}</span>
      <span class="status ${d.status}">${statusText}</span></div>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      <div style="font-size:12px;color:#666">${pct}%</div>`;
    if (d.error) html += `<div style="color:#f87171;font-size:13px;margin-top:4px">${d.error}</div>`;
    if (d.video_url) html += `<div class="video-result">
      <div id="video-placeholder-${d.task_id}" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:20px;text-align:center;cursor:pointer" onclick="loadVideo('${d.task_id}', '${d.video_url}')">
        <div style="color:#7c83ff;font-size:14px;margin-bottom:8px">🎬 点击加载视频</div>
        <div style="font-size:12px;color:#888">视频已生成，点击播放</div>
      </div>
      <div style="margin-top:6px"><a href="${d.video_url}" download style="color:#7c83ff;font-size:13px">下载视频</a>
        <button class="btn btn-sm" style="margin-left:12px;background:#2a1a4a;font-size:11px;padding:3px 10px" onclick="goPostproc('${d.task_id}')">后期处理</button></div></div>`;
    html += '</div>';
    box.innerHTML = html;
    // Also add to active tasks for polling if still running
    if (d.status === 'running' || d.status === 'queued') {
      activeTasks[d.task_id] = {...(activeTasks[d.task_id]||{type:'?',prompt:'(查询)'}), ...d};
      renderTasks(); pollTask(d.task_id);
    }
  } catch(e) { box.innerHTML = '<span style="color:#f87171">查询失败: ' + e.message + '</span>'; }
}

// Load all task history
let allHistoryTasks = [];
let allHistoryChains = [];
let currentHistoryFilter = 'all';

function filterHistory(type, btn) {
  currentHistoryFilter = type;

  // Update button states
  document.querySelectorAll('.query-subtab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  // Re-render with filter
  renderHistory();
}

function renderHistory() {
  const list = document.getElementById('history-list');
  let html = '';

  // Helper: check if chain is story mode
  const isStoryChain = c => c.params && c.params.story_mode;

  // Filter chains
  let filteredChains = allHistoryChains;
  if (currentHistoryFilter === 'chain') {
    filteredChains = allHistoryChains.filter(c => !isStoryChain(c));
  } else if (currentHistoryFilter === 'story') {
    filteredChains = allHistoryChains.filter(c => isStoryChain(c));
  } else if (currentHistoryFilter === 'all') {
    filteredChains = allHistoryChains;
  } else {
    filteredChains = [];
  }

  // Filter tasks
  let filteredTasks = allHistoryTasks;
  if (currentHistoryFilter === 'all') {
    filteredTasks = allHistoryTasks;
  } else if (currentHistoryFilter === 'chain' || currentHistoryFilter === 'story') {
    filteredTasks = [];
  } else {
    filteredTasks = allHistoryTasks.filter(t => t.mode === currentHistoryFilter);
  }

  // Render chains
  if (filteredChains.length) {
    html += filteredChains.map(c => {
      const statusCls = c.status === 'completed' ? 'completed' : c.status === 'failed' || c.status === 'partial' ? 'failed' : 'running';
      const statusMap = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败',partial:'部分完成'};
      const statusText = statusMap[c.status] || c.status;
      const promptText = c.params ? (c.params.prompt||'') : '';
      const timeStr = c.created_at ? new Date(c.created_at*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
      const durStr = c.created_at ? formatDuration(c.created_at, c.completed_at) : '';
      let extra = '';
      if (promptText) extra += `<div style="font-size:12px;color:#aaa;margin:4px 0;white-space:pre-wrap;word-break:break-all">${promptText}</div>`;

      // AI data display
      if (c.params) {
        if (c.params.ai_loras && c.params.ai_loras.length) {
          extra += `<div style="font-size:11px;color:#7c83ff;margin-bottom:2px">AI LoRA: ${c.params.ai_loras.map(l=>l.name+'('+l.strength+')').join(', ')}</div>`;
        }
        if (c.params.ai_prompt && c.params.ai_prompt !== promptText) {
          extra += `<div style="font-size:11px;color:#7c83ff;margin-bottom:2px;white-space:pre-wrap">AI优化: ${c.params.ai_prompt}</div>`;
        }
      }

      if (c.error) extra += `<div style="color:#f87171;font-size:12px;margin-top:4px">${c.error}</div>`;
      if (c.final_video_url) extra += `<div class="video-result" style="margin-top:8px">
        <div id="video-placeholder-chain-${c.chain_id}" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:16px;text-align:center;cursor:pointer" onclick="loadVideo('chain-${c.chain_id}', '${c.final_video_url}')">
          <div style="color:#7c83ff;font-size:14px;margin-bottom:6px">🎬 点击加载完整视频</div>
          <div style="font-size:11px;color:#888">避免自动加载，节省带宽</div>
        </div>
        <div style="margin-top:4px"><a href="${c.final_video_url}" download style="color:#7c83ff;font-size:13px">下载完整视频</a></div></div>`;

      // Segment details (collapsible)
      let segmentsHtml = '';
      if (c.segment_task_ids && c.segment_task_ids.length) {
        const segmentPrompts = c.params?.segment_prompts || [];
        segmentsHtml = `<div id="segments-${c.chain_id}" style="display:none;margin-top:8px;padding:8px;background:#0a1929;border-radius:4px">`;
        c.segment_task_ids.forEach((taskId, i) => {
          const segTask = window._historyTasks[taskId];
          const segParams = segTask?.params || {};
          const segPrompt = segParams.prompt || '';
          const segVideo = segTask?.video_url || '';
          const segStatus = segTask?.status || 'unknown';
          const segStatusText = {queued:'排队',running:'生成中',completed:'完成',failed:'失败'}[segStatus] || segStatus;
          const segStatusCls = {queued:'queued',running:'running',completed:'completed',failed:'failed'}[segStatus] || '';

          segmentsHtml += `<div style="margin-bottom:8px;padding:8px;background:#0a0a23;border-radius:4px;border-left:3px solid ${segStatus==='completed'?'#4ade80':segStatus==='failed'?'#f87171':'#7c83ff'}">`;
          segmentsHtml += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">`;
          segmentsHtml += `<span style="font-size:12px;color:#7c83ff">段 ${i+1}</span>`;
          segmentsHtml += `<span class="status ${segStatusCls}" style="font-size:10px;padding:2px 8px">${segStatusText}</span>`;
          segmentsHtml += `</div>`;

          if (segPrompt) {
            const targetPrompt = segmentPrompts[i] || segPrompt;
            segmentsHtml += `<div style="margin-bottom:4px;padding:4px;background:#1a2332;border-radius:2px">`;
            segmentsHtml += `<div style="font-size:10px;color:#888;margin-bottom:2px">🎯 Target Prompt:</div>`;
            segmentsHtml += `<div style="font-size:11px;color:#ccc;white-space:pre-wrap;word-break:break-all">${targetPrompt}</div>`;
            segmentsHtml += `</div>`;

            const vlmPrompt = segParams.vlm_prompt || '';
            const finalPrompt = segParams.final_prompt || segParams.ai_prompt || '';

            if (vlmPrompt && vlmPrompt !== targetPrompt) {
              segmentsHtml += `<div style="margin-bottom:4px;padding:4px;background:#1a2332;border-radius:2px">`;
              segmentsHtml += `<div style="font-size:10px;color:#888;margin-bottom:2px">🤖 VLM Generated:</div>`;
              segmentsHtml += `<div style="font-size:11px;color:#7c83ff;white-space:pre-wrap;word-break:break-all">${vlmPrompt}</div>`;
              segmentsHtml += `</div>`;
            }

            if (finalPrompt && finalPrompt !== vlmPrompt && finalPrompt !== targetPrompt) {
              segmentsHtml += `<div style="margin-bottom:4px;padding:4px;background:#1a2332;border-radius:2px">`;
              segmentsHtml += `<div style="font-size:10px;color:#888;margin-bottom:2px">✅ Final Prompt (实际使用):</div>`;
              segmentsHtml += `<div style="font-size:11px;color:#4ade80;white-space:pre-wrap;word-break:break-all">${finalPrompt}</div>`;
              segmentsHtml += `</div>`;
            }

            if (segVideo) {
              segmentsHtml += `<div style="margin-top:6px">`;
              segmentsHtml += `<div id="video-placeholder-seg-${taskId}" style="background:#0a0a23;border:1px solid #333;border-radius:4px;padding:12px;text-align:center;cursor:pointer" onclick="loadVideo('seg-${taskId}', '${segVideo}')">`;
              segmentsHtml += `<div style="color:#7c83ff;font-size:12px">🎬 点击加载视频</div>`;
              segmentsHtml += `</div>`;
              segmentsHtml += `</div>`;
            }
          }
          segmentsHtml += `</div>`;
        });
        segmentsHtml += `</div>`;
        extra += `<div style="margin-top:6px"><button class="btn btn-sm" style="background:#1a3a4a;font-size:11px;padding:3px 10px" onclick="toggleSegments('${c.chain_id}')">查看各段详情</button></div>`;
      }

      const chainCancelBtn = (c.status === 'running' || c.status === 'queued') ? `<button class="btn btn-sm" style="margin-left:8px;background:#4a1a1a;font-size:11px;padding:3px 10px" onclick="cancelChain('${c.chain_id}')">取消</button>` : '';
      const isStory = c.params && c.params.story_mode;
      const chainTypeLabel = isStory ? `<b style="color:#9d4edd">Story</b>` : `<b style="color:#7c83ff">长视频</b>`;
      const chainBorderColor = isStory ? '#9d4edd55' : '#7c83ff55';
      return `<div class="task-card" style="border-color:${chainBorderColor}">
        <div class="task-header"><span>${chainTypeLabel} <span style="color:#888;font-size:12px">${c.total_segments}段</span> <span style="color:#666;font-size:11px">${timeStr}</span>${durStr ? ` <span style="color:#4ade80;font-size:11px">⏱${durStr}</span>` : ''} <span class="task-id">${c.chain_id.substring(0,8)}</span>${chainCancelBtn}</span>
          <span class="status ${statusCls}">${statusText}</span></div>
        ${extra}${segmentsHtml}</div>`;
    }).join('');
  }

  // Render tasks
  if (filteredTasks.length) {
    html += filteredTasks.map(t => {
      const statusCls = (t.status || 'queued').toLowerCase();
      const statusText = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败'}[statusCls] || statusCls;
      const modeLabel = {t2v:'T2V',i2v:'I2V',extend:'EXTEND'}[t.mode] || t.mode || '';
      const modelLabel = t.model || '';
      const promptText = t.params ? (t.params.prompt||'') : '';
      const timeStr = t.created_at ? new Date(t.created_at*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
      const durStr = t.created_at ? formatDuration(t.created_at, t.completed_at) : '';

      // Check if it's a story task (legacy workflow-based)
      const isStory = t.params && t.params.workflow_name;
      const taskTypeLabel = isStory ? `<b style="color:#9d4edd">STORY</b>` : `<b>${modeLabel}</b>`;

      let extra = '';
      if (promptText) extra += `<div style="font-size:12px;color:#aaa;margin:4px 0;white-space:pre-wrap;word-break:break-all">${promptText}</div>`;

      if (t.params && t.params.ai_loras && t.params.ai_loras.length) {
        extra += `<div style="font-size:11px;color:#7c83ff;margin-bottom:2px">AI LoRA: ${t.params.ai_loras.map(l=>l.name+'('+l.strength+')').join(', ')}</div>`;
      }
      if (t.params && (t.params.final_prompt || t.params.ai_prompt)) {
        const dp = t.params.final_prompt || t.params.ai_prompt;
        extra += `<div style="font-size:11px;color:#7c83ff;margin-bottom:2px;white-space:pre-wrap">AI Prompt: ${dp}</div>`;
      }
      if (t.error) extra += `<div style="color:#f87171;font-size:12px;margin-top:4px">${t.error}</div>`;
      if (t.video_url) extra += `<div class="video-result" style="margin-top:8px">
        <div id="video-placeholder-task-${t.task_id}" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:16px;text-align:center;cursor:pointer" onclick="loadVideo('task-${t.task_id}', '${t.video_url}')">
          <div style="color:#7c83ff;font-size:14px;margin-bottom:6px">🎬 点击加载视频</div>
          <div style="font-size:11px;color:#888">避免自动加载，节省带宽</div>
        </div>
        <div style="margin-top:4px"><a href="${t.video_url}" download style="color:#7c83ff;font-size:13px">下载视频</a></div></div>`;

      // Build parameter details
      let paramsHtml = '';
      if (t.params) {
        const p = t.params;
        const fps = p.fps || 24;
        const frames = p.num_frames || 81;
        const duration = (frames / fps).toFixed(1);
        const resolution = `${p.width||'?'}×${p.height||'?'}`;
        const loraList = p.loras && p.loras.length ? p.loras.map(l=>`${l.name}(${l.strength})`).join(', ') : '无';
        const aiLoraList = p.ai_loras && p.ai_loras.length ? p.ai_loras.map(l=>`${l.name}(${l.strength})`).join(', ') : '';
        paramsHtml = `<div id="params-${t.task_id}" style="display:none;margin-top:8px;padding:8px;background:#0a1929;border-radius:4px;font-size:11px;line-height:1.6">
          <div><span style="color:#888">基础参数：</span>${resolution} | ${frames}帧(${duration}s) | ${fps}fps</div>
          <div><span style="color:#888">采样参数：</span>steps=${p.steps||20} | cfg=${p.cfg||6} | shift=${p.shift||5} | seed=${p.seed||-1}</div>
          <div><span style="color:#888">LoRA：</span>${loraList}</div>
          ${aiLoraList ? `<div><span style="color:#888">AI LoRA：</span>${aiLoraList}</div>` : ''}
          ${p.model_preset ? `<div><span style="color:#888">预设：</span>${p.model_preset}</div>` : ''}
        </div>`;
      }

      const rerunBtn = statusCls === 'completed' || statusCls === 'failed' ? `<button class="btn btn-sm" style="margin-left:8px;background:#1a4a2e;font-size:11px;padding:3px 10px" onclick="rerunTask('${t.task_id}')">重新生成</button>` : '';

      return `<div class="task-card">
        <div class="task-header"><span>${taskTypeLabel} <span style="color:#888;font-size:11px">${modelLabel}</span> <span style="color:#666;font-size:11px">${timeStr}</span>${durStr ? ` <span style="color:#4ade80;font-size:11px">⏱${durStr}</span>` : ''} <span class="task-id">${t.task_id.substring(0,8)}</span>${rerunBtn}</span>
          <span class="status ${statusCls}">${statusText}</span></div>
        ${extra}
        ${paramsHtml ? `<div style="margin-top:6px"><button class="btn btn-sm" style="background:#1a3a4a;font-size:11px;padding:3px 10px" onclick="toggleParams('${t.task_id}')">查看参数</button></div>` : ''}
        ${paramsHtml}</div>`;
    }).join('');
  }

  if (!html) {
    html = '<p style="color:#666;font-size:13px;text-align:center;padding:20px">暂无任务</p>';
  }

  list.innerHTML = html;
}

async function loadHistory() {
  const list = document.getElementById('history-list');
  list.innerHTML = '<span style="color:#aaa">加载中...</span>';
  try {
    const [tasksRes, chainsRes] = await Promise.all([
      fetch(BASE + '/api/v1/tasks', {headers:{'X-API-Key':getKey()}}),
      fetch(BASE + '/api/v1/chains', {headers:{'X-API-Key':getKey()}}),
    ]);
    if (!tasksRes.ok) { list.innerHTML = '<span style="color:#f87171">加载失败</span>'; return; }
    const tasks = await tasksRes.json();
    const chains = chainsRes.ok ? await chainsRes.json() : [];
    // Store for rerun
    window._historyTasks = {};
    tasks.forEach(t => window._historyTasks[t.task_id] = t);

    // Fetch segment tasks for each chain in parallel (N+1 fix)
    const segmentFetches = [];
    for (const chain of chains) {
      if (chain.segment_task_ids && chain.segment_task_ids.length) {
        for (const taskId of chain.segment_task_ids) {
          if (!window._historyTasks[taskId]) {
            segmentFetches.push(
              fetch(BASE + '/api/v1/tasks/' + taskId, {headers:{'X-API-Key':getKey()}})
                .then(r => r.ok ? r.json() : null)
                .then(segTask => { if (segTask) window._historyTasks[taskId] = segTask; })
                .catch(e => console.warn('Failed to fetch segment task', taskId, e))
            );
          }
        }
      }
    }
    if (segmentFetches.length) await Promise.all(segmentFetches);

    // Store data globally for filtering
    allHistoryTasks = tasks;
    allHistoryChains = chains;

    // Render with current filter
    renderHistory();
  } catch(e) { list.innerHTML = '<span style="color:#f87171">加载失败: ' + e.message + '</span>'; }
}

function rerunTask(taskId) {
  const t = window._historyTasks && window._historyTasks[taskId];
  if (!t || !t.params) { alert('无参数信息，无法重跑'); return; }
  const p = t.params;
  const mode = t.mode || 'i2v';
  const prefix = mode === 't2v' ? 't2v' : 'i2v';
  const fps = p.fps || 24;
  const duration = p.num_frames ? framesToDuration(p.num_frames, fps) : '3.3';
  // Fill form fields
  document.getElementById(prefix+'-prompt').value = p.prompt || '';
  document.getElementById(prefix+'-neg').value = p.negative_prompt || '';
  document.getElementById(prefix+'-model').value = p.model || 'a14b';
  document.getElementById(prefix+'-preset').value = p.model_preset || '';
  if (document.getElementById(prefix+'-t5preset')) {
    document.getElementById(prefix+'-t5preset').value = p.t5_preset || '';
  }
  document.getElementById(prefix+'-w').value = p.width || (mode==='t2v'?848:832);
  document.getElementById(prefix+'-h').value = p.height || 480;
  document.getElementById(prefix+'-duration').value = duration;
  document.getElementById(prefix+'-fps').value = fps;
  document.getElementById(prefix+'-steps').value = p.steps || 20;
  document.getElementById(prefix+'-cfg').value = p.cfg || 6.0;
  document.getElementById(prefix+'-shift').value = p.shift || 5.0;
  document.getElementById(prefix+'-seed').value = p.seed != null ? p.seed : -1;
  document.getElementById(prefix+'-sched').value = p.scheduler || 'unipc';
  if (mode === 'i2v' && document.getElementById('i2v-noise')) {
    document.getElementById('i2v-noise').value = p.noise_aug_strength || 0;
  }
  if (mode === 'i2v') {
    document.getElementById('i2v-motion').value = p.motion_amplitude || 0;
    document.getElementById('i2v-colormatch').value = p.color_match !== false ? 'true' : 'false';
    document.getElementById('i2v-colormatch-method').value = p.color_match_method || 'mkl';
    document.getElementById('i2v-resize').value = p.resize_mode || 'crop_to_new';
  }
  // Select matching LoRAs
  if (p.loras && p.loras.length) {
    const container = prefix + '-loras';
    document.querySelectorAll('#'+container+' .lora-item').forEach(item => {
      const cb = item.querySelector('input[type=checkbox]');
      const idx = parseInt(cb.dataset.idx);
      const match = p.loras.find(l => l.name === allLoras[idx]?.name);
      if (match) {
        cb.checked = true; item.classList.add('selected');
        item.querySelector('.lora-strength').value = match.strength;
      } else {
        cb.checked = false; item.classList.remove('selected');
      }
    });
  }
  // Switch to the right tab
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  const tabEl = document.querySelector(`.tab[data-tab="${mode}"]`);
  if (tabEl) {
    tabEl.classList.add('active');
  }
  const panelEl = document.getElementById('panel-'+mode);
  if (panelEl) {
    panelEl.classList.add('active');
  }
}

function toggleParams(taskId) {
  const el = document.getElementById('params-' + taskId);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

function toggleSegments(chainId) {
  const el = document.getElementById('segments-' + chainId);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// Load video on demand

// LoRA download
async function downloadLora() {
  const url = document.getElementById('dl-url').value.trim();
  const filename = document.getElementById('dl-filename').value.trim();
  const token = document.getElementById('dl-token').value.trim();
  if (!url) { alert('请输入下载链接'); return; }
  const box = document.getElementById('dl-status');
  box.innerHTML = '<div class="dl-item">正在获取文件信息...</div>';
  try {
    const r = await fetch(BASE + '/api/v1/loras/download', {
      method: 'POST', headers: {'Content-Type':'application/json','X-API-Key':getKey()},
      body: JSON.stringify({url, filename, token})
    });
    const d = await r.json();
    if (!r.ok) { box.innerHTML = `<div class="dl-item" style="border-color:#f87171">${d.detail || JSON.stringify(d)}</div>`; return; }
    box.innerHTML = `<div class="dl-item">下载中: <span class="dl-name">${d.filename}</span> (ID: ${d.download_id})</div>`;
    pollDownload(d.download_id, d.filename);
  } catch(e) { box.innerHTML = `<div class="dl-item" style="border-color:#f87171">请求失败: ${e.message}</div>`; }
}

async function pollDownload(dlId, filename) {
  const box = document.getElementById('dl-status');
  const poll = async () => {
    try {
      const r = await fetch(BASE + '/api/v1/loras/download/' + dlId, {headers:{'X-API-Key':getKey()}});
      const d = await r.json();
      if (d.status === 'completed') {
        box.innerHTML = `<div class="dl-item" style="border-color:#4ade80">完成: <span class="dl-name">${filename}</span></div>`;
        refreshLoraFiles();
        return;
      } else if (d.status === 'failed') {
        box.innerHTML = `<div class="dl-item" style="border-color:#f87171">失败: ${d.error}</div>`;
        return;
      }
    } catch(e) {}
    setTimeout(poll, 3000);
  };
  poll();
}

async function refreshLoraFiles() {
  try {
    const r = await fetch(BASE + '/api/v1/loras', {headers:{'X-API-Key':getKey()}});
    if (!r.ok) return;
    const data = await r.json();
    const files = data.loras || [];
    const list = document.getElementById('lora-file-list');
    if (!files.length) { list.innerHTML = '<span style="color:#666;font-size:13px">暂无文件</span>'; return; }
    list.innerHTML = files.map(f => `<div class="file-item"><span>${f.name}</span><span style="color:#888">${f.size_mb} MB</span></div>`).join('');
  } catch(e) {}
}

// CivitAI search
let civitaiNextCursor = '';
let _downloadedCivitaiIds = new Set(); // civitai_id set from local loras

async function loadCivitaiLoraFiles() {
  try {
    const r = await fetch(BASE + '/api/v1/loras', {headers:{'X-API-Key':getKey()}});
    if (r.ok) {
      const lorasData = await r.json();
      _downloadedCivitaiIds = new Set((lorasData.loras || []).map(l => l.civitai_id).filter(Boolean));
    }
  } catch(e) {}
}

function isCivitaiDownloaded(modelId) {
  return _downloadedCivitaiIds.has(modelId);
}

function _buildCivitaiUrl(cursor) {
  const query = document.getElementById('civitai-query').value.trim() || 'wan 2.1';
  const baseModel = document.getElementById('civitai-basemodel').value;
  const sort = document.getElementById('civitai-sort').value;
  const nsfw = document.getElementById('civitai-nsfw').checked;
  let url = BASE + '/api/v1/civitai/search?query=' + encodeURIComponent(query) + '&limit=100&nsfw=' + nsfw + '&sort=' + encodeURIComponent(sort);
  if (baseModel) url += '&base_model=' + encodeURIComponent(baseModel);
  if (cursor) url += '&cursor=' + encodeURIComponent(cursor);
  return url;
}

async function searchCivitAI(cursor) {
  cursor = cursor || '';
  const box = document.getElementById('civitai-results');
  const query = document.getElementById('civitai-query').value.trim();

  // Detect CivitAI URL: extract model ID and fetch directly
  const urlMatch = query.match(/civitai\.com\/models\/(\d+)/);
  if (urlMatch && !cursor) {
    const modelId = urlMatch[1];
    box.innerHTML = '<span style="color:#aaa">加载模型...</span>';
    await loadCivitaiLoraFiles();
    try {
      const r = await fetch(BASE + '/api/v1/civitai/models/' + modelId, {headers:{'X-API-Key':getKey()}});
      if (!r.ok) { box.innerHTML = '<span style="color:#f87171">模型不存在或加载失败</span>'; return; }
      const model = await r.json();
      civitaiNextCursor = '';
      renderCivitAIResults({items: [model]}, false);
    } catch(e) { box.innerHTML = '<span style="color:#f87171">加载失败: ' + e.message + '</span>'; }
    return;
  }

  if (!cursor) {
    box.innerHTML = '<span style="color:#aaa">搜索中...</span>';
  }
  await loadCivitaiLoraFiles();
  try {
    const r = await fetch(_buildCivitaiUrl(cursor), {headers:{'X-API-Key':getKey()}});
    if (!r.ok) { box.innerHTML = '<span style="color:#f87171">搜索失败</span>'; return; }
    const data = await r.json();
    civitaiNextCursor = data.next_cursor || '';
    renderCivitAIResults(data, !!cursor);
  } catch(e) { box.innerHTML = '<span style="color:#f87171">搜索失败: ' + e.message + '</span>'; }
}

function renderCivitAIResults(data, append) {
  const box = document.getElementById('civitai-results');
  if (!data.items || !data.items.length) {
    if (!append) box.innerHTML = '<span style="color:#666">无结果</span>';
    return;
  }
  const cardsHtml = data.items.map(m => {
    const downloaded = isCivitaiDownloaded(m.id);
    const isVideo = m.preview_url && /\.(mp4|webm)$/i.test(m.preview_url);
    const previewClick = m.preview_url ? `onclick="openPreviewModal('${m.preview_url}', ${isVideo})"` : '';
    let previewHtml;
    if (m.preview_url && isVideo) {
      const playIcon = `<svg viewBox="0 0 24 24" style="width:32px;height:32px;fill:#fff;opacity:0.85"><path d="M8 5v14l11-7z"/></svg>`;
      previewHtml = `<div style="position:relative;width:100%;height:140px;cursor:pointer" onclick="var v=this.querySelector('video'),b=this.querySelector('.civ-play-btn');if(v.paused){v.play();b.style.opacity='0'}else{v.pause();b.style.opacity='1'}" ondblclick="event.stopPropagation();openPreviewModal('${m.preview_url}',true)"><video data-src="${m.preview_url}" muted loop playsinline preload="none" referrerpolicy="no-referrer" style="width:100%;height:140px;object-fit:cover"></video><div class="civ-play-btn" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.35);transition:opacity .2s">${playIcon}</div></div>`;
    } else if (m.preview_url) {
      previewHtml = `<img data-src="${m.preview_url}" alt="${m.name}" referrerpolicy="no-referrer" style="cursor:pointer" ${previewClick}>`;
    } else {
      previewHtml = '<div style="height:140px;background:#111;display:flex;align-items:center;justify-content:center;color:#444;font-size:12px">No Preview</div>';
    }
    const tags = (m.tags||[]).slice(0,4).join(', ');
    const dl = m.stats ? (m.stats.downloadCount||0).toLocaleString() : '0';
    const versions = m.versions||[];
    const tw = versions.length && versions[0].trained_words ? versions[0].trained_words.slice(0,3).join(', ') : '';
    // Build file options: merge HIGH/LOW pairs
    const allFiles = [];
    versions.forEach(v => {
      (v.files||[]).forEach(f => {
        allFiles.push({name: f.name, url: f.download_url, size: f.size_mb, version: v.name, base: v.base_model});
      });
    });
    // Group: try to pair HIGH/LOW by matching base name
    const groups = [];
    const used = new Set();
    for (let i = 0; i < allFiles.length; i++) {
      if (used.has(i)) continue;
      const f = allFiles[i];
      const isH = /[-_](H|HIGH)[-_.]|[-_](H|HIGH)$/i.test(f.name);
      const isL = /[-_](L|LOW)[-_.]|[-_](L|LOW)$/i.test(f.name);
      if (isH || isL) {
        // Find the counterpart
        const pairRe = isH ? /[-_](L|LOW)[-_.]|[-_](L|LOW)$/i : /[-_](H|HIGH)[-_.]|[-_](H|HIGH)$/i;
        const pairIdx = allFiles.findIndex((g, j) => j !== i && !used.has(j) && pairRe.test(g.name));
        if (pairIdx >= 0) {
          used.add(i); used.add(pairIdx);
          const hFile = isH ? f : allFiles[pairIdx];
          const lFile = isH ? allFiles[pairIdx] : f;
          const baseName = hFile.name.replace(/[-_](H|HIGH)[-_.]/i, '').replace('.safetensors','');
          groups.push({label: baseName + ' (H+L ' + (hFile.size+lFile.size).toFixed(0) + 'MB)', files: [hFile, lFile], base: hFile.base});
          continue;
        }
      }
      used.add(i);
      const label = f.name.replace('.safetensors','');
      groups.push({label: label + ' (' + f.size + 'MB)', files: [f], base: f.base});
    }
    // If no file groups (Meilisearch), show version info as placeholder
    if (!groups.length && versions.length) {
      const v = versions[0];
      groups.push({label: v.base_model || v.name || 'LoRA', files: [], base: v.base_model});
    }
    const fileOpts = groups.map((g,i) => `<option value="${i}">${g.label}</option>`).join('') || '<option>点击下载获取文件列表</option>';
    const badgeHtml = downloaded ? '<span class="card-badge">已下载</span>' : '';
    const dlCls = downloaded ? ' downloaded' : '';
    return `<div class="civitai-card${dlCls}" data-model-id="${m.id}" data-groups='${JSON.stringify(groups).replace(/'/g,"&#39;")}'>
      <input type="checkbox" class="card-check" data-mid="${m.id}" onchange="updateCivitaiBatch()">
      ${badgeHtml}
      ${previewHtml}
      <div class="card-body">
        <div class="card-title" title="${m.name}">${m.name}</div>
        ${tags ? `<div class="card-tags">${tags}</div>` : ''}
        <div class="card-meta">DL: ${dl}${versions[0] ? ' | '+versions[0].base_model : ''}</div>
        ${tw ? `<div class="card-tw" title="${tw}">${tw}</div>` : ''}
        <div class="card-actions">
          <select id="cv-${m.id}">${fileOpts}</select>
          ${downloaded ? '<span style="color:#4ade80;font-size:11px;padding:3px 6px">已下载</span>' : `<button id="dlbtn-${m.id}" class="btn btn-sm" style="padding:3px 10px;font-size:11px" onclick="downloadFromCivitAI(${m.id})">下载</button>`}
        </div>
        <div id="dlst-${m.id}" class="card-dl-status" style="font-size:11px;color:#aaa;padding:2px 8px;display:none"></div>
      </div>
    </div>`;
  }).join('');

  if (append) {
    // Append to existing grid
    const grid = box.querySelector('.civitai-grid');
    if (grid) {
      grid.insertAdjacentHTML('beforeend', cardsHtml);
    } else {
      box.innerHTML = '<div class="civitai-grid">' + cardsHtml + '</div>';
    }
  } else {
    box.innerHTML = '<div class="civitai-grid">' + cardsHtml + '</div>';
  }
  // Activate lazy loading for preview media
  observeLazyMedia(box);
  // Pagination
  const pages = document.getElementById('civitai-pages');
  pages.innerHTML = civitaiNextCursor
    ? `<button class="btn btn-sm" style="background:#333" onclick="searchCivitAI('${civitaiNextCursor}')">加载更多</button>`
    : '';
  updateCivitaiBatch();
}

function toggleCivitaiDownloaded() {
  const hide = document.getElementById('civitai-hide-downloaded').checked;
  document.querySelectorAll('.civitai-card.downloaded').forEach(c => c.style.display = hide ? 'none' : '');
}

function updateCivitaiBatch() {
  const checks = document.querySelectorAll('.card-check:checked');
  const bar = document.getElementById('civitai-batch');
  document.getElementById('civitai-sel-count').textContent = checks.length;
  bar.style.display = checks.length > 0 ? 'flex' : 'none';
}

function clearCivitAISelection() {
  document.querySelectorAll('.card-check').forEach(c => c.checked = false);
  updateCivitaiBatch();
}

async function batchDownloadCivitAI() {
  const checks = document.querySelectorAll('.card-check:checked');
  if (!checks.length) return;
  const box = document.getElementById('civitai-dl-status');
  box.innerHTML += `<div class="dl-item">开始批量下载 ${checks.length} 个模型...</div>`;
  for (const cb of checks) {
    const mid = parseInt(cb.dataset.mid);
    await downloadFromCivitAI(mid);
  }
  clearCivitAISelection();
}

async function downloadFromCivitAI(modelId) {
  const card = document.querySelector(`.civitai-card[data-model-id="${modelId}"]`);
  if (!card) return;
  const st = document.getElementById('dlst-' + modelId);
  const btn = document.getElementById('dlbtn-' + modelId);
  const _setStatus = (msg, color) => { if (st) { st.style.display = ''; st.style.color = color || '#aaa'; st.textContent = msg; } };
  let groups = JSON.parse(card.dataset.groups || '[]');

  // If no file groups (Meilisearch result), fetch full model data first
  if (!groups.length || !groups[0].files || !groups[0].files.length) {
    if (btn) { btn.disabled = true; btn.textContent = '获取中...'; }
    _setStatus('正在获取模型文件信息...');
    try {
      const r = await fetch(BASE + '/api/v1/civitai/models/' + modelId, {headers:{'X-API-Key':getKey()}});
      if (!r.ok) { _setStatus('获取模型信息失败', '#f87171'); if (btn) { btn.disabled = false; btn.textContent = '下载'; } return; }
      const model = await r.json();
      const allFiles = [];
      (model.versions||[]).forEach(v => {
        (v.files||[]).forEach(f => {
          allFiles.push({name: f.name, url: f.download_url, size: f.size_mb, version: v.name, base: v.base_model});
        });
      });
      const newGroups = []; const used = new Set();
      for (let i = 0; i < allFiles.length; i++) {
        if (used.has(i)) continue;
        const f = allFiles[i];
        const isH = /[-_](H|HIGH)[-_.]|[-_](H|HIGH)$/i.test(f.name);
        const isL = /[-_](L|LOW)[-_.]|[-_](L|LOW)$/i.test(f.name);
        if (isH || isL) {
          const pairRe = isH ? /[-_](L|LOW)[-_.]|[-_](L|LOW)$/i : /[-_](H|HIGH)[-_.]|[-_](H|HIGH)$/i;
          const pairIdx = allFiles.findIndex((g, j) => j !== i && !used.has(j) && pairRe.test(g.name));
          if (pairIdx >= 0) {
            used.add(i); used.add(pairIdx);
            const hFile = isH ? f : allFiles[pairIdx];
            const lFile = isH ? allFiles[pairIdx] : f;
            newGroups.push({label: hFile.name.replace(/[-_](H|HIGH)[-_.]/i,'').replace('.safetensors','') + ' (H+L)', files: [hFile, lFile], base: hFile.base});
            continue;
          }
        }
        used.add(i);
        newGroups.push({label: f.name.replace('.safetensors','') + ' (' + f.size + 'MB)', files: [f], base: f.base});
      }
      groups = newGroups;
      card.dataset.groups = JSON.stringify(groups);
      const selEl = document.getElementById('cv-' + modelId);
      if (selEl) selEl.innerHTML = groups.map((g,i) => `<option value="${i}">${g.label}</option>`).join('');
      if (!groups.length) { _setStatus('未找到可下载文件', '#f87171'); if (btn) { btn.disabled = false; btn.textContent = '下载'; } return; }
      if (groups.length > 1) { _setStatus('请选择版本后再点下载'); if (btn) { btn.disabled = false; btn.textContent = '下载'; } return; }
    } catch(e) {
      _setStatus(e.message, '#f87171'); if (btn) { btn.disabled = false; btn.textContent = '下载'; } return;
    }
  }

  const sel = document.getElementById('cv-' + modelId);
  const idx = sel ? parseInt(sel.value) : 0;
  const group = groups[idx];
  if (!group || !group.files.length) return;
  if (btn) { btn.disabled = true; btn.textContent = '下载中...'; }
  _setStatus('下载中: ' + group.files.map(f => f.name).join(', '));
  for (const file of group.files) {
    try {
      const r = await fetch(BASE + '/api/v1/civitai/download', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':getKey()},
        body: JSON.stringify({model_id: modelId, download_url: file.url, filename: file.name})
      });
      const d = await r.json();
      if (!r.ok) { _setStatus(d.detail || JSON.stringify(d), '#f87171'); if (btn) { btn.disabled = false; btn.textContent = '下载'; } return; }
      pollCivitAIDl(d.download_id, file.name, modelId);
    } catch(e) { _setStatus(e.message, '#f87171'); if (btn) { btn.disabled = false; btn.textContent = '下载'; } }
  }
}

async function downloadAllFiles(modelId) {
  // Legacy: redirect to downloadFromCivitAI which now handles groups
  await downloadFromCivitAI(modelId);
}

// openPreviewModal, closePreviewModal are in common.js

async function pollCivitAIDl(dlId, filename, modelId) {
  const st = modelId ? document.getElementById('dlst-' + modelId) : null;
  const btn = modelId ? document.getElementById('dlbtn-' + modelId) : null;
  const poll = async () => {
    try {
      const r = await fetch(BASE + '/api/v1/civitai/download/' + dlId, {headers:{'X-API-Key':getKey()}});
      const d = await r.json();
      if (d.status === 'completed') {
        if (st) { st.style.color = '#4ade80'; st.textContent = '下载完成'; }
        if (btn) { btn.textContent = '已下载'; btn.disabled = true; btn.style.background = '#333'; }
        loadLoras();
        return;
      } else if (d.status === 'failed') {
        if (st) { st.style.color = '#f87171'; st.textContent = '失败: ' + d.error; }
        if (btn) { btn.disabled = false; btn.textContent = '下载'; }
        return;
      }
    } catch(e) {}
    setTimeout(poll, 3000);
  };
  poll();
}

async function maybeOptimizePrompt(prompt, loraNames, mode) {
  try {
    const r = await fetch(BASE + '/api/v1/prompt/optimize', {
      method:'POST', headers:{'Content-Type':'application/json','X-API-Key':getKey()},
      body: JSON.stringify({prompt, lora_names: loraNames, mode})
    });
    if (!r.ok) { const d = await r.json(); alert('优化失败: ' + (d.detail||'')); return prompt; }
    const d = await r.json();
    return await openPromptModal(d.original_prompt, d.optimized_prompt, d.explanation, d.trigger_words_used||[]);
  } catch(e) { alert('优化请求失败: ' + e.message); return prompt; }
}

async function optimizePrompt(mode, btn) {
  const promptEl = document.getElementById(mode + '-prompt');
  const prompt = promptEl.value.trim();
  if (!prompt) { alert('请先输入提示词'); return; }
  const loras = getSelectedLoras(mode + '-loras');
  const duration = parseFloat(document.getElementById(mode + '-duration').value) || 3.3;
  btn.disabled = true; btn.textContent = '优化中...';
  try {
    const body = {prompt, lora_names: loras.map(l=>l.name), mode, duration};
    // For I2V, include the image for scene understanding
    if (mode === 'i2v') {
      const previewImg = document.getElementById('i2v-img-preview');
      if (previewImg && previewImg.src && previewImg.style.display !== 'none') {
        body.image_base64 = previewImg.src;
      }
    }
    const r = await fetch(BASE + '/api/v1/prompt/optimize', {
      method:'POST', headers:{'Content-Type':'application/json','X-API-Key':getKey()},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) { alert('优化失败: ' + (d.detail||'')); return; }
    const box = document.getElementById(mode + '-preview');
    document.getElementById(mode + '-preview-text').textContent = d.optimized_prompt;
    const explainEl = document.getElementById(mode + '-preview-explain');
    let explainText = d.explanation || '';
    if (d.trigger_words_used && d.trigger_words_used.length) {
      explainText += (explainText ? '\n' : '') + 'Trigger words: ' + d.trigger_words_used.join(', ');
    }
    explainEl.textContent = explainText;
    box.style.display = 'block';
    box.dataset.optimized = d.optimized_prompt;
  } catch(e) { alert('优化请求失败: ' + e.message); }
  finally { btn.disabled = false; btn.textContent = 'AI 优化 Prompt'; }
}

function applyPreview(mode) {
  const box = document.getElementById(mode + '-preview');
  const promptEl = document.getElementById(mode + '-prompt');
  promptEl.value = box.dataset.optimized || '';
  box.style.display = 'none';
}

function dismissPreview(mode) {
  document.getElementById(mode + '-preview').style.display = 'none';
}

async function recommendLoras(mode, btn) {
  const prompt = document.getElementById(mode + '-prompt').value.trim();
  if (!prompt) { alert('请先输入提示词'); return; }
  btn.disabled = true; btn.textContent = 'AI 分析中...';
  try {
    const r = await fetch(BASE + '/api/v1/loras/recommend', {
      method:'POST', headers:{'Content-Type':'application/json','X-API-Key':getKey()},
      body: JSON.stringify({prompt})
    });
    const d = await r.json();
    if (!r.ok) { alert('推荐失败: ' + (d.detail||'')); return; }
    const recommended = d.loras || [];
    if (!recommended.length) { alert('未找到匹配的 LoRA'); return; }
    // Clear current selections then apply recommendations
    const cid = mode + '-loras';
    document.querySelectorAll('#' + cid + ' .lora-item').forEach(item => {
      const cb = item.querySelector('input[type=checkbox]');
      const idx = parseInt(cb.dataset.idx);
      const match = recommended.find(l => l.name === allLoras[idx]?.name);
      if (match) {
        cb.checked = true; item.classList.add('selected');
        item.querySelector('.lora-strength').value = match.strength;
        item.style.boxShadow = '0 0 0 2px #7c83ff'; setTimeout(() => item.style.boxShadow = '', 2000);
      } else {
        cb.checked = false; item.classList.remove('selected');
      }
    });
  } catch(e) { alert('推荐请求失败: ' + e.message); }
  finally { btn.disabled = false; btn.textContent = 'AI 推荐 LoRA'; }
}

// --- Extend task ---
function extendTask(taskId) {
  // Find task from active or history
  const t = activeTasks[taskId] || (window._historyTasks && window._historyTasks[taskId]);
  if (!t) { alert('找不到任务信息'); return; }
  // Switch to I2V tab and fill form
  const p = t.params || {};
  document.getElementById('i2v-prompt').value = p.prompt || '';
  document.getElementById('i2v-neg').value = p.negative_prompt || '';
  document.getElementById('i2v-model').value = p.model || 'a14b';
  document.getElementById('i2v-preset').value = p.model_preset || '';
  if (document.getElementById('i2v-t5preset')) {
    document.getElementById('i2v-t5preset').value = p.t5_preset || '';
  }
  document.getElementById('i2v-w').value = p.width || 832;
  document.getElementById('i2v-h').value = p.height || 480;
  const fps = p.fps || 24;
  document.getElementById('i2v-duration').value = p.num_frames ? framesToDuration(p.num_frames, fps) : '3.3';
  document.getElementById('i2v-fps').value = fps;
  document.getElementById('i2v-steps').value = p.steps || 20;
  document.getElementById('i2v-cfg').value = p.cfg || 6.0;
  document.getElementById('i2v-shift').value = p.shift || 5.0;
  document.getElementById('i2v-seed').value = -1;
  document.getElementById('i2v-sched').value = p.scheduler || 'unipc';
  document.getElementById('i2v-noise').value = 0.05;
  document.getElementById('i2v-motion').value = p.motion_amplitude || 0;
  document.getElementById('i2v-colormatch').value = p.color_match !== false ? 'true' : 'false';
  document.getElementById('i2v-colormatch-method').value = p.color_match_method || 'mkl';
  document.getElementById('i2v-resize').value = p.resize_mode || 'crop_to_new';
  // Select matching LoRAs
  if (p.loras && p.loras.length) {
    const cid = 'i2v-loras';
    document.querySelectorAll('#'+cid+' .lora-item').forEach(item => {
      const cb = item.querySelector('input[type=checkbox]');
      const idx = parseInt(cb.dataset.idx);
      const match = p.loras.find(l => l.name === allLoras[idx]?.name);
      if (match) { cb.checked = true; item.classList.add('selected'); item.querySelector('.lora-strength').value = match.strength; }
      else { cb.checked = false; item.classList.remove('selected'); }
    });
  }
  // Hide image upload, show extend info
  const uploadArea = document.getElementById('i2v-upload-area');
  uploadArea.innerHTML = `<div style="color:#4ade80;font-size:13px;text-align:center">
    <div style="margin-bottom:6px">延续模式</div>
    <div style="font-size:11px;color:#aaa">将自动提取上一段视频最后一帧</div>
    <div style="font-size:11px;color:#888;margin-top:4px">父任务: ${taskId.substring(0,8)}</div>
    <button class="btn btn-sm" style="margin-top:8px;background:#333;font-size:11px;padding:3px 10px" onclick="resetI2VUpload()">取消延续</button>
  </div>`;
  window._extendParentId = taskId;
  // Change submit button
  const btnRow = document.querySelector('#panel-i2v .btn-row');
  const submitBtn = btnRow.querySelector('.btn:not(.btn-sm):not([type=checkbox])');
  if (submitBtn) { submitBtn.textContent = '延续生成'; submitBtn.setAttribute('onclick', 'submitExtend()'); }
  // Switch tab
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  document.querySelector('.tab[data-tab="i2v"]').classList.add('active');
  document.getElementById('panel-i2v').classList.add('active');
}

function resetI2VUpload() {
  window._extendParentId = null;
  const uploadArea = document.getElementById('i2v-upload-area');
  uploadArea.innerHTML = `<img id="i2v-img-preview" style="display:none">
    <span id="i2v-upload-text">点击或拖拽上传图片</span>
    <input type="file" id="i2v-file" accept="image/*">`;
  document.getElementById('i2v-file').addEventListener('change', function() {
    const file = this.files[0]; if (!file) return;
    i2vSelectedFile = file;
    const reader = new FileReader();
    const preview = document.getElementById('i2v-img-preview');
    const text = document.getElementById('i2v-upload-text');
    reader.onload = e => { preview.src = e.target.result; preview.style.display = 'block'; text.style.display = 'none'; };
    reader.readAsDataURL(file);
  });
  const btnRow = document.querySelector('#panel-i2v .btn-row');
  const submitBtn = btnRow.querySelector('.btn:not(.btn-sm):not([type=checkbox])');
  if (submitBtn) { submitBtn.textContent = '生成视频'; submitBtn.setAttribute('onclick', 'submitI2V()'); }
}

async function submitExtend() {
  const parentId = window._extendParentId;
  if (!parentId) { alert('无延续父任务'); return; }
  const prompt = document.getElementById('i2v-prompt').value;
  if (!prompt) { alert('请输入提示词'); return; }
  const fps = parseInt(document.getElementById('i2v-fps').value);
  const duration = parseFloat(document.getElementById('i2v-duration').value);
  const seed = parseInt(document.getElementById('i2v-seed').value);
  const loras = getSelectedLoras('i2v-loras');
  const body = {
    parent_task_id: parentId,
    prompt,
    negative_prompt: document.getElementById('i2v-neg').value,
    num_frames: durationToFrames(duration, fps),
    steps: parseInt(document.getElementById('i2v-steps').value),
    cfg: parseFloat(document.getElementById('i2v-cfg').value),
    shift: parseFloat(document.getElementById('i2v-shift').value),
    seed: seed >= 0 ? seed : null,
    scheduler: document.getElementById('i2v-sched').value,
    noise_aug_strength: parseFloat(document.getElementById('i2v-noise').value),
    loras: loras,
    auto_prompt: document.getElementById('i2v-auto-prompt').checked,
    concat_with_parent: true,
  };
  try {
    const r = await fetch(BASE + '/api/v1/generate/extend', {
      method: 'POST', headers: {'Content-Type':'application/json','X-API-Key':getKey()},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) { alert('Error: ' + (d.detail || JSON.stringify(d))); return; }
    addTask(d.task_id, 'EXTEND', prompt);
    resetI2VUpload();
  } catch(e) { alert('请求失败: ' + e.message); }
}

// --- Chain (long video) ---

// Toggle Story mode fields visibility
// Story mode is now always enabled, this function is kept for compatibility
function toggleStoryMode(setDefaults) {
  // Story mode is always on, no need to check checkbox
  const isStory = true;
  // Show story-specific fields (always visible now)
  document.getElementById('chain-story-motion-frames-field').style.display = '';
  document.getElementById('chain-story-boundary-field').style.display = '';
  document.getElementById('chain-story-clip-field').style.display = '';
  document.getElementById('chain-story-match-ratio-field').style.display = '';
  document.getElementById('chain-postproc-row').style.display = '';
  document.getElementById('chain-image-mode-row').style.display = '';
  // Hide fields not applicable to story mode
  const noiseField = document.getElementById('chain-noise')?.closest('.field');
  const colormatchField = document.getElementById('chain-colormatch')?.closest('.field');
  const colormatchMethodField = document.getElementById('chain-colormatch-method')?.closest('.field');
  if (noiseField) noiseField.style.display = isStory ? 'none' : '';
  if (colormatchField) colormatchField.style.display = isStory ? 'none' : '';
  if (colormatchMethodField) colormatchMethodField.style.display = isStory ? 'none' : '';
  // Always enforce Story mode defaults when enabled (overrides localStorage)
  if (isStory) {
    document.getElementById('chain-steps').value = '5';
    document.getElementById('chain-fps').value = '16';
    // Only set other defaults on first toggle (not on localStorage restore)
    if (setDefaults) {
      document.getElementById('chain-shift').value = '8.0';
      document.getElementById('chain-cfg').value = '1.0';
      document.getElementById('chain-sched').value = 'euler';
      document.getElementById('chain-motion').value = '1.15';
    }
  }
}

// Toggle post-processing sub-options visibility
function togglePostProc() {
  const upscaleOn = document.getElementById('chain-enable-upscale').checked;
  document.getElementById('chain-upscale-model-field').style.display = upscaleOn ? '' : 'none';
  // Show resize dropdown only for TRT models (PyTorch RealESRGAN is fixed 2x)
  const model = document.getElementById('chain-upscale-model').value;
  const isTRT = model !== 'RealESRGAN_x2plus.pth';
  document.getElementById('chain-upscale-resize-field').style.display = (upscaleOn && isTRT) ? '' : 'none';
  document.getElementById('chain-interpolation-mult-field').style.display = document.getElementById('chain-enable-interpolation').checked ? '' : 'none';
  document.getElementById('chain-mmaudio-row').style.display = document.getElementById('chain-enable-mmaudio').checked ? '' : 'none';
}

// When chain upscale model changes, toggle resize field visibility
function onChainUpscaleModelChange() {
  const model = document.getElementById('chain-upscale-model').value;
  const isTRT = model !== 'RealESRGAN_x2plus.pth';
  document.getElementById('chain-upscale-resize-field').style.display = isTRT ? '' : 'none';
}

// When post-processing upscale model changes, toggle resize field
function onPpUpscaleModelChange() {
  const model = document.getElementById('pp-upscale-model').value;
  const isTRT = model !== 'RealESRGAN_x2plus.pth';
  document.getElementById('pp-upscale-resize').disabled = !isTRT;
  if (!isTRT) document.getElementById('pp-upscale-resize').value = '2x';
}

// Toggle collapsible sections
function toggleCollapsible(el) {
  const content = el.nextElementSibling;
  const icon = el.querySelector('#collapse-icon');
  if (content.classList.contains('active')) {
    content.classList.remove('active');
    icon.textContent = '▼';
  } else {
    content.classList.add('active');
    icon.textContent = '▲';
  }
}

// Add a new segment
function addSegment() {
  segmentCounter++;
  const container = document.getElementById('segments-container');
  const segmentDiv = document.createElement('div');
  segmentDiv.className = 'segment-card';
  segmentDiv.dataset.segmentId = segmentCounter;

  segmentDiv.innerHTML = `
    <div class="segment-header">
      <span class="segment-title">分段 ${segmentCounter}</span>
      <div style="display:flex;gap:8px">
        <button class="btn btn-sm" style="background:#0f3460;padding:4px 12px" onclick="generateSingleSegment(${segmentCounter})">单独生成</button>
        <button class="segment-remove" onclick="removeSegment(${segmentCounter})">删除</button>
      </div>
    </div>
    <div class="row">
      <div class="field" style="flex:2">
        <label>提示词</label>
        <textarea id="seg-${segmentCounter}-prompt" placeholder="描述这一段的内容..." style="min-height:60px" onchange="saveSegmentsToStorage()"></textarea>
        <div style="margin-top:6px;display:flex;gap:8px">
          <button class="btn btn-sm" style="background:#0f3460;padding:4px 12px" onclick="optimizeSegmentPrompt(${segmentCounter}, this)">AI 优化 Prompt</button>
          <button class="btn btn-sm" style="background:#0f3460;padding:4px 12px" onclick="recommendSegmentLoras(${segmentCounter}, this)">AI 推荐 LoRA</button>
        </div>
      </div>
      <div class="field" style="flex:0;min-width:100px">
        <label>时长(秒)</label>
        <input type="number" id="seg-${segmentCounter}-duration" value="3.3" step="0.1" min="0.5" max="10" onchange="saveSegmentsToStorage()">
      </div>
    </div>
    <div style="margin-bottom:12px">
      <button class="btn btn-sm" style="background:#16213e;padding:6px 14px" onclick="toggleSegmentPreview(${segmentCounter})">
        <span id="seg-${segmentCounter}-preview-toggle">▼ 查看参考图</span>
      </button>
      <button class="btn btn-sm" style="background:#1a4a2e;padding:6px 14px;margin-left:8px" onclick="analyzeAndContinue(${segmentCounter}, this)">
        🔍 VLM 分析续写
      </button>
      <button class="btn btn-sm" style="background:#2e1a4a;padding:6px 14px;margin-left:8px" onclick="describeSegmentImage(${segmentCounter}, this)">
        📝 仅描述图片
      </button>
    </div>
    <div id="seg-${segmentCounter}-preview-area" style="display:none;margin-bottom:12px;padding:12px;background:#0a0a23;border:1px solid #444;border-radius:6px">
      <div style="color:#888;font-size:12px;margin-bottom:8px">参考图（上一段的最后一帧）</div>
      <div id="seg-${segmentCounter}-preview-content" style="color:#666;font-size:13px">暂无参考图</div>
      <div id="seg-${segmentCounter}-description" style="margin-top:12px;padding:10px;background:#16213e;border-radius:4px;display:none">
        <div style="color:#7c83ff;font-size:12px;margin-bottom:6px">VLM 图片描述：</div>
        <div id="seg-${segmentCounter}-description-text" style="color:#e0e0e0;font-size:13px;line-height:1.5"></div>
      </div>
    </div>
    <div class="lora-section">
      <label style="cursor:pointer;user-select:none" onclick="toggleSegLoras(${segmentCounter}, this)">▶ LoRA 选择（可选）</label>
      <div class="lora-grid" id="seg-${segmentCounter}-loras" style="display:none"></div>
    </div>
    <div id="seg-${segmentCounter}-status" style="margin-top:12px"></div>
  `;

  container.appendChild(segmentDiv);

  // Don't render LoRAs immediately - they load on expand via toggleSegLoras()

  saveSegmentsToStorage();
}

// Remove a segment
function removeSegment(id) {
  const segment = document.querySelector(`[data-segment-id="${id}"]`);
  if (segment) {
    segment.remove();
    saveSegmentsToStorage();
  }
}

// Toggle segment LoRA section (lazy load)
function toggleSegLoras(segId, labelEl) {
  const grid = document.getElementById(`seg-${segId}-loras`);
  if (!grid) return;
  const isHidden = grid.style.display === 'none';
  grid.style.display = isHidden ? '' : 'none';
  labelEl.textContent = (isHidden ? '▼' : '▶') + ' LoRA 选择（可选）';
  // Render LoRAs on first expand
  if (isHidden && grid.children.length === 0 && allLoras && allLoras.length > 0) {
    renderLoras(`seg-${segId}-loras`);
    // Restore pending selections if any
    const pending = window['_pendingSegLoras_' + segId];
    if (pending) {
      delete window['_pendingSegLoras_' + segId];
      setTimeout(() => {
        pending.forEach(lora => {
          const idx = allLoras.findIndex(l => l.name === lora.name);
          if (idx >= 0) {
            const item = document.getElementById(`seg-${segId}-loras-${idx}`);
            if (item) {
              item.querySelector('input[type=checkbox]').checked = true;
              item.querySelector('.lora-strength').value = lora.strength;
              item.classList.add('selected');
            }
          }
        });
      }, 50);
    }
  }
}

// Toggle segment preview area
function toggleSegmentPreview(segId) {
  const previewArea = document.getElementById(`seg-${segId}-preview-area`);
  const toggleText = document.getElementById(`seg-${segId}-preview-toggle`);

  if (previewArea.style.display === 'none') {
    previewArea.style.display = 'block';
    toggleText.textContent = '▲ 隐藏参考图';

    // Load preview image if not already loaded
    loadSegmentPreview(segId);
  } else {
    previewArea.style.display = 'none';
    toggleText.textContent = '▼ 查看参考图';
  }
}

// Load preview image for a segment
async function loadSegmentPreview(segId) {
  const previewContent = document.getElementById(`seg-${segId}-preview-content`);

  // Find the previous segment or use the initial image
  const allSegments = Array.from(document.querySelectorAll('.segment-card'));
  const currentIndex = allSegments.findIndex(s => s.dataset.segmentId === String(segId));

  if (currentIndex === 0) {
    // First segment - check if there's an uploaded image
    const chainImgPreview = document.getElementById('chain-img-preview');
    if (chainSelectedFile) {
      const reader = new FileReader();
      reader.onload = function(e) {
        previewContent.innerHTML = `<img src="${e.target.result}" style="max-width:100%;max-height:200px;border-radius:6px">`;
      };
      reader.readAsDataURL(chainSelectedFile);
    } else {
      previewContent.innerHTML = '<span style="color:#666">第一段，无参考图（可在上方上传首帧图片）</span>';
    }
  } else {
    // Not first segment - get last frame from previous segment
    const prevSegment = allSegments[currentIndex - 1];
    const prevSegId = prevSegment.dataset.segmentId;
    const prevTaskId = window[`seg_${prevSegId}_task_id`];
    const prevLastFrameUrl = window[`seg_${prevSegId}_last_frame_url`];

    // If we already have the last frame URL, display it directly
    if (prevLastFrameUrl) {
      previewContent.innerHTML = `
        <div style="color:#888;font-size:12px;margin-bottom:8px">上一段（分段 ${prevSegId}）的最后一帧</div>
        <img src="${prevLastFrameUrl}" style="max-width:100%;max-height:200px;border-radius:6px">
        <div style="margin-top:6px;font-size:12px;color:#7c83ff">提示：点击"VLM 分析续写"可基于此图生成续写提示词</div>
      `;
      return;
    }

    // If previous segment has a task, try to get last frame from task info
    if (prevTaskId) {
      previewContent.innerHTML = '<div style="color:#7c83ff;font-size:13px">正在加载最后一帧...</div>';
      try {
        const r = await fetch(BASE + '/api/v1/tasks/' + prevTaskId, {headers:{'X-API-Key':getKey()}});
        if (r.ok) {
          const task = await r.json();
          if (task.last_frame_url) {
            window[`seg_${prevSegId}_last_frame_url`] = toLocalUrl(task.last_frame_url);
            previewContent.innerHTML = `
              <div style="color:#888;font-size:12px;margin-bottom:8px">上一段（分段 ${prevSegId}）的最后一帧</div>
              <img src="${toLocalUrl(task.last_frame_url)}" style="max-width:100%;max-height:200px;border-radius:6px">
              <div style="margin-top:6px;font-size:12px;color:#7c83ff">提示：点击"VLM 分析续写"可基于此图生成续写提示词</div>
            `;
            return;
          }
        }
      } catch(e) {
        console.error('Failed to load last frame from task:', e);
      }
    }

    previewContent.innerHTML = `<span style="color:#666">上一段（分段 ${prevSegId}）尚未生成或最后一帧未提取</span>`;
  }
}

// Optimize prompt for a specific segment
async function optimizeSegmentPrompt(segId, btn) {
  const promptEl = document.getElementById(`seg-${segId}-prompt`);
  const prompt = promptEl.value;

  if (!prompt) {
    alert('请先输入提示词');
    return;
  }

  const originalText = btn.textContent;
  btn.textContent = '优化中...';
  btn.disabled = true;

  try {
    const loras = getSelectedLoras(`seg-${segId}-loras`);
    const duration = parseFloat(document.getElementById(`seg-${segId}-duration`).value);

    const r = await fetch(BASE + '/api/v1/prompt/optimize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-API-Key': getKey()},
      body: JSON.stringify({
        prompt: prompt,
        lora_names: loras.map(l => l.name),
        mode: 't2v',
        duration: duration
      })
    });

    if (!r.ok) {
      const err = await r.json();
      alert('优化失败: ' + (err.detail || JSON.stringify(err)));
      return;
    }

    const data = await r.json();
    if (data.optimized_prompt && data.optimized_prompt !== prompt) {
      promptEl.value = data.optimized_prompt;

      // Show explanation in description area
      const descArea = document.getElementById(`seg-${segId}-description`);
      const descText = document.getElementById(`seg-${segId}-description-text`);
      if (descText) descText.innerHTML = `<div style="color:#4ade80;margin-bottom:6px">✓ 提示词已优化</div>${data.explanation || ''}`;
      if (descArea) descArea.style.display = 'block';

      // Auto-expand preview area
      const previewArea = document.getElementById(`seg-${segId}-preview-area`);
      const toggleText = document.getElementById(`seg-${segId}-preview-toggle`);
      if (previewArea) previewArea.style.display = 'block';
      if (toggleText) toggleText.textContent = '▲ 隐藏参考图';

      saveSegmentsToStorage();
    } else {
      const descArea = document.getElementById(`seg-${segId}-description`);
      const descText = document.getElementById(`seg-${segId}-description-text`);
      if (descText) descText.innerHTML = '<div style="color:#888">提示词已经很好，无需优化</div>';
      if (descArea) descArea.style.display = 'block';

      const previewArea = document.getElementById(`seg-${segId}-preview-area`);
      const toggleText = document.getElementById(`seg-${segId}-preview-toggle`);
      if (previewArea) previewArea.style.display = 'block';
      if (toggleText) toggleText.textContent = '▲ 隐藏参考图';
    }
  } catch(e) {
    alert('请求失败: ' + e.message);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

// Recommend LoRAs for a specific segment
async function recommendSegmentLoras(segId, btn) {
  const promptEl = document.getElementById(`seg-${segId}-prompt`);
  const prompt = promptEl.value;

  if (!prompt) {
    alert('请先输入提示词');
    return;
  }

  const originalText = btn.textContent;
  btn.textContent = '推荐中...';
  btn.disabled = true;

  try {
    const r = await fetch(BASE + '/api/v1/loras/recommend', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-API-Key': getKey()},
      body: JSON.stringify({prompt: prompt})
    });

    if (!r.ok) {
      const err = await r.json();
      alert('推荐失败: ' + (err.detail || JSON.stringify(err)));
      return;
    }

    const data = await r.json();
    if (data.loras && data.loras.length > 0) {
      // Auto-select recommended LoRAs
      const containerPrefix = `seg-${segId}-loras`;
      data.loras.forEach(lora => {
        const idx = allLoras.findIndex(l => l.name === lora.name);
        if (idx >= 0) {
          const item = document.getElementById(`${containerPrefix}-${idx}`);
          if (item) {
            const cb = item.querySelector('input[type=checkbox]');
            const strengthInput = item.querySelector('.lora-strength');
            cb.checked = true;
            strengthInput.value = lora.strength;
            item.classList.add('selected');
          }
        }
      });

      // Show result in description area
      const descArea = document.getElementById(`seg-${segId}-description`);
      const descText = document.getElementById(`seg-${segId}-description-text`);
      const loraNames = data.loras.map(l => l.name).join(', ');
      descText.innerHTML = `<div style="color:#4ade80;margin-bottom:6px">✓ 已推荐并选中 ${data.loras.length} 个 LoRA</div><div style="color:#888;font-size:12px">${loraNames}</div>`;
      descArea.style.display = 'block';

      const previewArea = document.getElementById(`seg-${segId}-preview-area`);
      const toggleText = document.getElementById(`seg-${segId}-preview-toggle`);
      previewArea.style.display = 'block';
      toggleText.textContent = '▲ 隐藏参考图';

      saveSegmentsToStorage();
    } else {
      const descArea = document.getElementById(`seg-${segId}-description`);
      const descText = document.getElementById(`seg-${segId}-description-text`);
      descText.innerHTML = '<div style="color:#888">未找到合适的 LoRA 推荐</div>';
      descArea.style.display = 'block';

      const previewArea = document.getElementById(`seg-${segId}-preview-area`);
      const toggleText = document.getElementById(`seg-${segId}-preview-toggle`);
      previewArea.style.display = 'block';
      toggleText.textContent = '▲ 隐藏参考图';
    }
  } catch(e) {
    alert('请求失败: ' + e.message);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

// Generate a single segment
async function generateSingleSegment(segId) {
  const promptEl = document.getElementById(`seg-${segId}-prompt`);
  const prompt = promptEl.value;

  if (!prompt) {
    alert('请输入提示词');
    return;
  }

  const duration = parseFloat(document.getElementById(`seg-${segId}-duration`).value);
  const loras = getSelectedLoras(`seg-${segId}-loras`);

  // Get global parameters
  const seed = parseInt(document.getElementById('chain-seed').value);
  const params = {
    prompt: prompt,
    negative_prompt: document.getElementById('chain-neg').value,
    model: document.getElementById('chain-model').value,
    model_preset: document.getElementById('chain-preset').value,
    t5_preset: document.getElementById('chain-t5preset').value,
    width: parseInt(document.getElementById('chain-w').value),
    height: parseInt(document.getElementById('chain-h').value),
    num_frames: durationToFrames(duration, parseInt(document.getElementById('chain-fps').value)),
    fps: parseInt(document.getElementById('chain-fps').value),
    steps: parseInt(document.getElementById('chain-steps').value),
    cfg: parseFloat(document.getElementById('chain-cfg').value),
    shift: parseFloat(document.getElementById('chain-shift').value),
    seed: seed >= 0 ? seed : null,
    scheduler: document.getElementById('chain-sched').value,
    loras: loras,
  };

  const statusBox = document.getElementById(`seg-${segId}-status`);
  statusBox.innerHTML = '<div style="color:#aaa;font-size:13px">提交中...</div>';

  try {
    // Story Mode is always enabled
    const isStoryMode = true;

    // Check if this is the first segment and has an uploaded image
    const allSegments = Array.from(document.querySelectorAll('.segment-card'));
    const currentIndex = allSegments.findIndex(s => s.dataset.segmentId === String(segId));

    console.log(`[generateSingleSegment] segId=${segId}, currentIndex=${currentIndex}, totalSegments=${allSegments.length}, isStoryMode=${isStoryMode}`);

    let endpoint = BASE + '/api/v1/generate';
    let requestBody;
    let headers = {'X-API-Key': getKey()};

    // Story Mode: use Chain API for segments 2+
    if (isStoryMode && currentIndex > 0) {
      const prevSegment = allSegments[currentIndex - 1];
      const prevSegId = prevSegment.dataset.segmentId;
      const prevLastFrameUrl = window[`seg_${prevSegId}_last_frame_url`];
      const prevVideoUrl = window[`seg_${prevSegId}_video_url`];

      console.log(`[generateSingleSegment] Story Mode segment 2+: prevSegId=${prevSegId}, prevLastFrameUrl=${prevLastFrameUrl}, prevVideoUrl=${prevVideoUrl}`);
      console.log(`[generateSingleSegment] All window seg variables:`, Object.keys(window).filter(k => k.startsWith('seg_')));

      if (!prevVideoUrl) {
        statusBox.innerHTML = `<div style="color:#f87171;font-size:13px">错误：上一段（分段 ${prevSegId}）尚未生成视频，请先生成上一段</div>`;
        return;
      }

      // Get initial reference image (from localStorage or first segment)
      let initialRefImage = null;
      const cachedInitialRef = localStorage.getItem('chain_initial_ref_image');

      if (cachedInitialRef) {
        try {
          const blob = await fetch(cachedInitialRef).then(r => r.blob());
          initialRefImage = blob;
        } catch(e) {
          console.warn('Failed to fetch cached initial ref:', e);
        }
      }

      if (!initialRefImage && chainSelectedFile) {
        initialRefImage = chainSelectedFile;
      }

      console.log(`[generateSingleSegment] initialRefImage=${initialRefImage ? 'found' : 'null'}, cachedInitialRef=${cachedInitialRef ? 'exists' : 'null'}`);

      if (!initialRefImage) {
        statusBox.innerHTML = `<div style="color:#f87171;font-size:13px">错误：Story 模式需要首帧图片作为身份参考，请上传首帧图片</div>`;
        return;
      }

      // Use Chain API with Story Mode parameters
      endpoint = BASE + '/api/v1/generate/chain';

      const chainParams = {
        segments: [{
          prompt: prompt,
          duration: duration
        }],
        model: params.model,
        model_preset: params.model_preset,
        t5_preset: params.t5_preset,
        width: params.width,
        height: params.height,
        fps: params.fps,
        steps: params.steps,
        cfg: params.cfg,
        shift: params.shift,
        seed: params.seed,
        scheduler: params.scheduler,
        loras: params.loras,
        upscale: params.upscale,
        story_mode: true,
        motion_frames: parseInt(document.getElementById('chain-story-motion-frames').value),
        boundary: parseFloat(document.getElementById('chain-story-boundary').value),
        clip_preset: document.getElementById('chain-story-clip').value,
        match_image_ratio: document.getElementById('chain-story-match-ratio').checked,
        enable_upscale: document.getElementById('chain-enable-upscale').checked,
        upscale_model: document.getElementById('chain-upscale-model').value,
        upscale_resize: document.getElementById('chain-upscale-resize').value,
        enable_interpolation: document.getElementById('chain-enable-interpolation').checked,
        interpolation_multiplier: parseInt(document.getElementById('chain-interpolation-multiplier').value),
        interpolation_profile: 'small',
        enable_mmaudio: document.getElementById('chain-enable-mmaudio').checked,
        mmaudio_prompt: document.getElementById('chain-mmaudio-prompt').value,
        mmaudio_negative_prompt: document.getElementById('chain-mmaudio-neg').value,
        mmaudio_steps: parseInt(document.getElementById('chain-mmaudio-steps').value),
        mmaudio_cfg: parseFloat(document.getElementById('chain-mmaudio-cfg').value),
        auto_continue: false,
        transition: 'none',
        parent_video_url: prevVideoUrl || null
      };

      const fd = new FormData();

      // Add initial reference image for identity consistency
      fd.append('initial_reference_image', initialRefImage, 'initial_ref.png');
      fd.append('params', JSON.stringify(chainParams));
      requestBody = fd;

      console.log(`[generateSingleSegment] Using Chain API for Story Mode segment 2+`);

    } else if (currentIndex === 0) {
      // First segment
      console.log(`[generateSingleSegment] First segment, chainSelectedFile=${chainSelectedFile ? 'exists' : 'null'}, isStoryMode=${isStoryMode}`);
      if (isStoryMode && chainSelectedFile) {
        // Story Mode: use Chain API even for first segment (enables post-processing pipeline)
        endpoint = BASE + '/api/v1/generate/chain';

        const imageMode = document.querySelector('input[name="chain-image-mode"]:checked').value;
        const chainParams = {
          segments: [{
            prompt: prompt,
            duration: duration
          }],
          model: params.model,
          model_preset: params.model_preset,
          t5_preset: params.t5_preset,
          width: params.width,
          height: params.height,
          fps: params.fps,
          steps: params.steps,
          cfg: params.cfg,
          shift: params.shift,
          seed: params.seed,
          scheduler: params.scheduler,
          loras: params.loras,
          upscale: params.upscale,
          story_mode: true,
          image_mode: imageMode,
          face_swap_strength: parseFloat(document.getElementById('chain-face-swap-strength').value),
          motion_frames: parseInt(document.getElementById('chain-story-motion-frames').value),
          boundary: parseFloat(document.getElementById('chain-story-boundary').value),
          clip_preset: document.getElementById('chain-story-clip').value,
          match_image_ratio: document.getElementById('chain-story-match-ratio').checked,
          enable_upscale: document.getElementById('chain-enable-upscale').checked,
          upscale_model: document.getElementById('chain-upscale-model').value,
          upscale_resize: document.getElementById('chain-upscale-resize').value,
          enable_interpolation: document.getElementById('chain-enable-interpolation').checked,
          interpolation_multiplier: parseInt(document.getElementById('chain-interpolation-multiplier').value),
          interpolation_profile: 'small',
          enable_mmaudio: document.getElementById('chain-enable-mmaudio').checked,
          mmaudio_prompt: document.getElementById('chain-mmaudio-prompt').value,
          mmaudio_negative_prompt: document.getElementById('chain-mmaudio-neg').value,
          mmaudio_steps: parseInt(document.getElementById('chain-mmaudio-steps').value),
          mmaudio_cfg: parseFloat(document.getElementById('chain-mmaudio-cfg').value),
          auto_continue: false,
          transition: 'none',
        };

        const fd = new FormData();
        if (imageMode === 'face_reference') {
          fd.append('face_image', chainSelectedFile);
        } else {
          fd.append('image', chainSelectedFile);
        }
        console.log(`[generateSingleSegment] seg0 story: imageMode=${imageMode}, file field=${imageMode === 'face_reference' ? 'face_image' : 'image'}`);
        fd.append('params', JSON.stringify(chainParams));
        requestBody = fd;

        // Cache initial reference image for Story Mode
        const reader = new FileReader();
        reader.onload = (e) => {
          localStorage.setItem('chain_initial_ref_image', e.target.result);
        };
        reader.readAsDataURL(chainSelectedFile);

      } else if (chainSelectedFile) {
        // Non-Story Mode: Use I2V with uploaded image
        endpoint = BASE + '/api/v1/generate/i2v';
        params.noise_aug_strength = parseFloat(document.getElementById('chain-noise').value);
        params.motion_amplitude = parseFloat(document.getElementById('chain-motion').value);
        params.color_match = document.getElementById('chain-colormatch').value === 'true';
        params.color_match_method = document.getElementById('chain-colormatch-method').value;
        params.resize_mode = document.getElementById('chain-resize').value;

        const fd = new FormData();
        fd.append('image', chainSelectedFile);
        fd.append('params', JSON.stringify(params));
        requestBody = fd;
      } else {
        // T2V without image
        headers['Content-Type'] = 'application/json';
        requestBody = JSON.stringify(params);
      }
    } else {
      // Second segment and beyond (non-Story Mode) - MUST use previous segment's last frame
      console.log(`[generateSingleSegment] Non-Story Mode segment 2+`);
      const prevSegment = allSegments[currentIndex - 1];
      const prevSegId = prevSegment.dataset.segmentId;
      const prevLastFrameUrl = window[`seg_${prevSegId}_last_frame_url`];

      console.log(`[generateSingleSegment] prevSegId=${prevSegId}, prevLastFrameUrl=${prevLastFrameUrl}`);

      if (!prevLastFrameUrl) {
        statusBox.innerHTML = `<div style="color:#f87171;font-size:13px">错误：上一段（分段 ${prevSegId}）尚未生成或最后一帧未提取，请先生成上一段</div>`;
        return;
      }

      // Use I2V with previous segment's last frame
      endpoint = BASE + '/api/v1/generate/i2v';
      params.noise_aug_strength = parseFloat(document.getElementById('chain-noise').value);
      params.motion_amplitude = parseFloat(document.getElementById('chain-motion').value);
      params.color_match = document.getElementById('chain-colormatch').value === 'true';
      params.color_match_method = document.getElementById('chain-colormatch-method').value;
      params.resize_mode = document.getElementById('chain-resize').value;

      const fd = new FormData();

      try {
        const imgBlob = await fetch(prevLastFrameUrl).then(r => r.blob());
        fd.append('image', imgBlob, 'last_frame.png');
      } catch(e) {
        statusBox.innerHTML = `<div style="color:#f87171;font-size:13px">获取上一段最后一帧失败: ${e.message}</div>`;
        return;
      }

      fd.append('params', JSON.stringify(params));
      requestBody = fd;
    }

    const r = await fetch(endpoint, {
      method: 'POST',
      headers: headers,
      body: requestBody
    });

    const data = await r.json();
    if (!r.ok) {
      statusBox.innerHTML = `<div style="color:#f87171;font-size:13px">${data.detail || JSON.stringify(data)}</div>`;
      return;
    }

    // Check if this is a Chain API response (has chain_id) or standard task response (has task_id)
    const isChainResponse = !!data.chain_id;
    const displayId = isChainResponse ? data.chain_id.substring(0, 8) : data.task_id.substring(0, 8);

    statusBox.innerHTML = `<div style="color:#4ade80;font-size:13px">已提交任务: ${displayId}</div>`;

    if (isChainResponse) {
      // Store chain ID for this segment
      window[`seg_${segId}_chain_id`] = data.chain_id;
      saveSegmentsToStorage();
      // Poll chain status
      pollSegmentChain(segId, data.chain_id);
    } else {
      // Store task ID for this segment
      window[`seg_${segId}_task_id`] = data.task_id;
      saveSegmentsToStorage();
      // Poll task status
      pollSegmentTask(segId, data.task_id);
    }
  } catch(e) {
    statusBox.innerHTML = `<div style="color:#f87171;font-size:13px">请求失败: ${e.message}</div>`;
  }
}

// Poll single segment task status
async function pollSegmentTask(segId, taskId) {
  const statusBox = document.getElementById(`seg-${segId}-status`);

  const poll = async () => {
    try {
      const r = await fetch(BASE + `/api/v1/tasks/${taskId}`, {
        headers: {'X-API-Key': getKey()}
      });
      if (!r.ok) return;

      const task = await r.json();
      const progress = Math.round((task.progress || 0) * 100);
      const statusCls = {queued:'queued',running:'running',completed:'completed',failed:'failed'}[task.status] || '';
      const statusText = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败'}[task.status] || task.status;

      let html = `<div style="font-size:13px">
        <span class="status ${statusCls}">${statusText}</span>
        <span style="color:#888;margin-left:8px">${task.task_id.substring(0, 8)}</span>
      </div>`;

      if (task.status === 'running') {
        html += `<div class="progress-bar" style="margin-top:6px"><div class="progress-fill" style="width:${progress}%"></div></div>`;
        html += `<div style="font-size:12px;color:#666;margin-top:4px">${progress}%</div>`;
      }

      if (task.error) {
        html += `<div style="color:#f87171;font-size:12px;margin-top:4px">${task.error}</div>`;
      }

      if (task.video_url) {
        // Calculate duration if we have timestamps
        let durationText = '';
        if (task.created_at && task.completed_at) {
          const duration = task.completed_at - task.created_at;
          const minutes = Math.floor(duration / 60);
          const seconds = duration % 60;
          durationText = `<div style="font-size:12px;color:#888;margin-top:4px">⏱️ 生成时长: ${minutes}分${seconds}秒</div>`;
        }

        html += `<div class="video-result" style="margin-top:8px">
          <div id="video-placeholder-poll-seg-${segId}" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:16px;text-align:center;cursor:pointer" onclick="loadVideo('poll-seg-${segId}', '${task.video_url}')">
            <div style="color:#7c83ff;font-size:14px;margin-bottom:6px">🎬 点击加载视频</div>
            <div style="font-size:11px;color:#888">避免自动加载</div>
          </div>
          <div style="margin-top:4px"><a href="${task.video_url}" download style="color:#7c83ff;font-size:12px">下载视频</a></div>
          ${durationText}
        </div>`;

        // Store video URL and last frame URL for this segment
        window[`seg_${segId}_video_url`] = task.video_url;
        console.log(`[pollSegmentTask] Segment ${segId} completed, video_url=${task.video_url}`);
        if (task.last_frame_url) {
          window[`seg_${segId}_last_frame_url`] = toLocalUrl(task.last_frame_url);
          console.log(`[pollSegmentTask] Segment ${segId} last_frame_url stored: ${toLocalUrl(task.last_frame_url)}`);
        } else {
          console.warn(`[pollSegmentTask] Segment ${segId} has NO last_frame_url in task response!`);
        }
        saveSegmentsToStorage();
        console.log(`[pollSegmentTask] Segment ${segId} data saved to storage`);
      }

      statusBox.innerHTML = html;

      if (task.status === 'completed' || task.status === 'failed') return;
    } catch(e) {}

    setTimeout(poll, 2000);
  };

  poll();
}

// Poll single segment chain status (for Story Mode segments 2+)
async function pollSegmentChain(segId, chainId) {
  const statusBox = document.getElementById(`seg-${segId}-status`);

  const poll = async () => {
    try {
      const r = await fetch(BASE + `/api/v1/chains/${chainId}`, {
        headers: {'X-API-Key': getKey()}
      });
      if (!r.ok) return;

      const chain = await r.json();
      const statusCls = {queued:'queued',running:'running',completed:'completed',failed:'failed',partial:'failed'}[chain.status] || '';
      const statusText = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败',partial:'部分完成'}[chain.status] || chain.status;

      let html = `<div style="font-size:13px">
        <span class="status ${statusCls}">${statusText}</span>
        <span style="color:#888;margin-left:8px">${chainId.substring(0, 8)}</span>
      </div>`;

      if (chain.status === 'running') {
        const completed = chain.completed_segments || 0;
        const total = chain.total_segments || 1;
        const progress = Math.round((completed / total) * 100);
        html += `<div class="progress-bar" style="margin-top:6px"><div class="progress-fill" style="width:${progress}%"></div></div>`;
        html += `<div style="font-size:12px;color:#666;margin-top:4px">${completed}/${total} 段</div>`;
      }

      if (chain.error) {
        html += `<div style="color:#f87171;font-size:12px;margin-top:4px">${chain.error}</div>`;
      }

      if (chain.final_video_url) {
        // Calculate duration if we have timestamps
        let durationText = '';
        if (chain.created_at && chain.completed_at) {
          const duration = parseInt(chain.completed_at) - parseInt(chain.created_at);
          const minutes = Math.floor(duration / 60);
          const seconds = duration % 60;
          durationText = `<div style="font-size:12px;color:#888;margin-top:4px">⏱️ 生成时长: ${minutes}分${seconds}秒</div>`;
        }

        html += `<div class="video-result" style="margin-top:8px">
          <div id="video-placeholder-poll-seg-${segId}" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:16px;text-align:center;cursor:pointer" onclick="loadVideo('poll-seg-${segId}', '${chain.final_video_url}')">
            <div style="color:#7c83ff;font-size:14px;margin-bottom:6px">🎬 点击加载视频</div>
            <div style="font-size:11px;color:#888">避免自动加载</div>
          </div>
          <div style="margin-top:4px"><a href="${chain.final_video_url}" download style="color:#7c83ff;font-size:12px">下载视频</a></div>
          ${durationText}
        </div>`;

        // Store video URL for this segment
        window[`seg_${segId}_video_url`] = chain.final_video_url;

        // Get last frame URL from the segment task
        if (chain.segment_task_ids && chain.segment_task_ids.length > 0) {
          const taskId = chain.segment_task_ids[0];
          try {
            const taskR = await fetch(BASE + `/api/v1/tasks/${taskId}`, {
              headers: {'X-API-Key': getKey()}
            });
            if (taskR.ok) {
              const task = await taskR.json();
              if (task.last_frame_url) {
                window[`seg_${segId}_last_frame_url`] = toLocalUrl(task.last_frame_url);
              }
            }
          } catch(e) {
            console.warn('Failed to fetch task for last frame:', e);
          }
        }

        saveSegmentsToStorage();
      }

      statusBox.innerHTML = html;

      if (chain.status === 'completed' || chain.status === 'failed' || chain.status === 'partial') return;
    } catch(e) {}

    setTimeout(poll, 2000);
  };

  poll();
}

// Analyze previous segment and generate continuation prompt
async function analyzeAndContinue(segId, btn) {
  const allSegments = Array.from(document.querySelectorAll('.segment-card'));
  const currentIndex = allSegments.findIndex(s => s.dataset.segmentId === String(segId));

  if (currentIndex === 0) {
    // First segment - check if there's an uploaded image
    if (!chainSelectedFile) {
      alert('第一段没有参考图，请先上传首帧图片');
      return;
    }

    const originalText = btn.textContent;
    btn.textContent = '分析中...';
    btn.disabled = true;

    try {
      // Convert uploaded image to base64
      const reader = new FileReader();
      reader.onload = async function(e) {
        const base64 = e.target.result.split(',')[1];

        try {
          const r = await fetch(BASE + '/api/v1/prompt/optimize', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-API-Key': getKey()},
            body: JSON.stringify({
              prompt: '描述这张图片，并建议接下来的动作',
              mode: 'i2v',
              image_base64: base64,
              duration: parseFloat(document.getElementById(`seg-${segId}-duration`).value)
            })
          });

          if (!r.ok) {
            const err = await r.json();
            alert('分析失败: ' + (err.detail || JSON.stringify(err)));
            return;
          }

          const data = await r.json();
          if (data.optimized_prompt) {
            document.getElementById(`seg-${segId}-prompt`).value = data.optimized_prompt;
            alert('已根据首帧图片生成提示词');
          }
        } catch(e) {
          alert('请求失败: ' + e.message);
        } finally {
          btn.textContent = originalText;
          btn.disabled = false;
        }
      };
      reader.readAsDataURL(chainSelectedFile);
    } catch(e) {
      alert('读取图片失败: ' + e.message);
      btn.textContent = originalText;
      btn.disabled = false;
    }
    return;
  }

  // Not first segment - need to get previous segment's last frame
  const prevSegment = allSegments[currentIndex - 1];
  const prevSegId = prevSegment.dataset.segmentId;
  const prevLastFrameUrl = window[`seg_${prevSegId}_last_frame_url`];
  const prevTaskId = window[`seg_${prevSegId}_task_id`];

  if (!prevLastFrameUrl && !prevTaskId) {
    alert('上一段视频尚未生成，请先生成上一段');
    return;
  }

  const originalText = btn.textContent;
  btn.textContent = '分析中...';
  btn.disabled = true;

  try {
    let base64;
    let lastFrameUrl = prevLastFrameUrl;

    // If we don't have the last frame URL cached, try to get it from task
    if (!lastFrameUrl && prevTaskId) {
      const taskR = await fetch(BASE + '/api/v1/tasks/' + prevTaskId, {headers:{'X-API-Key':getKey()}});
      if (taskR.ok) {
        const task = await taskR.json();
        if (task.last_frame_url) {
          lastFrameUrl = toLocalUrl(task.last_frame_url);
          window[`seg_${prevSegId}_last_frame_url`] = lastFrameUrl;
        }
      }
    }

    if (!lastFrameUrl) {
      alert('无法获取上一段的最后一帧，请稍后重试');
      btn.textContent = originalText;
      btn.disabled = false;
      return;
    }

    // Fetch the last frame image and convert to base64
    const imgBlob = await fetch(lastFrameUrl).then(r => r.blob());
    const reader = new FileReader();
    base64 = await new Promise((resolve) => {
      reader.onload = (e) => resolve(e.target.result.split(',')[1]);
      reader.readAsDataURL(imgBlob);
    });

    // Get previous segment's prompt for context
    const prevPrompt = document.getElementById(`seg-${prevSegId}-prompt`).value;

    // Call VLM to analyze and generate continuation
    const r = await fetch(BASE + '/api/v1/prompt/optimize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-API-Key': getKey()},
      body: JSON.stringify({
        prompt: `前一段的提示词是: "${prevPrompt}". 基于这个最后一帧，建议接下来的动作和场景`,
        mode: 'i2v',
        image_base64: base64,
        duration: parseFloat(document.getElementById(`seg-${segId}-duration`).value)
      })
    });

    if (!r.ok) {
      const err = await r.json();
      alert('分析失败: ' + (err.detail || JSON.stringify(err)));
      return;
    }

    const data = await r.json();
    if (data.optimized_prompt) {
      document.getElementById(`seg-${segId}-prompt`).value = data.optimized_prompt;

      // Also show the last frame in preview area
      const previewContent = document.getElementById(`seg-${segId}-preview-content`);
      const lastFrameUrl = window[`seg_${prevSegId}_last_frame`] || (cachedFrame || `data:image/jpeg;base64,${base64}`);
      previewContent.innerHTML = `
        <div style="color:#888;font-size:12px;margin-bottom:8px">上一段（分段 ${prevSegId}）的最后一帧</div>
        <img src="${lastFrameUrl}" style="max-width:100%;max-height:200px;border-radius:6px">
      `;

      // Show result in description area
      const descArea = document.getElementById(`seg-${segId}-description`);
      const descText = document.getElementById(`seg-${segId}-description-text`);
      descText.innerHTML = `<div style="color:#4ade80;margin-bottom:6px">✓ 已根据上一段视频生成续写提示词</div>${data.explanation || ''}`;
      descArea.style.display = 'block';

      // Auto-expand preview area
      const previewArea = document.getElementById(`seg-${segId}-preview-area`);
      const toggleText = document.getElementById(`seg-${segId}-preview-toggle`);
      previewArea.style.display = 'block';
      toggleText.textContent = '▲ 隐藏参考图';

      saveSegmentsToStorage();
    }
  } catch(e) {
    alert('分析失败: ' + e.message);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

// Describe image only (without generating prompt)
async function describeSegmentImage(segId, btn) {
  const allSegments = Array.from(document.querySelectorAll('.segment-card'));
  const currentIndex = allSegments.findIndex(s => s.dataset.segmentId === String(segId));

  let imageBase64 = null;

  if (currentIndex === 0) {
    // First segment - use uploaded image
    if (!chainSelectedFile) {
      alert('第一段没有参考图，请先上传首帧图片');
      return;
    }

    const reader = new FileReader();
    await new Promise((resolve) => {
      reader.onload = function(e) {
        imageBase64 = e.target.result.split(',')[1];
        resolve();
      };
      reader.readAsDataURL(chainSelectedFile);
    });
  } else {
    // Not first segment - get last frame from previous segment
    const prevSegment = allSegments[currentIndex - 1];
    const prevSegId = prevSegment.dataset.segmentId;
    const prevLastFrameUrl = window[`seg_${prevSegId}_last_frame_url`];
    const prevTaskId = window[`seg_${prevSegId}_task_id`];

    if (!prevLastFrameUrl && !prevTaskId) {
      alert('上一段视频尚未生成，请先生成上一段');
      return;
    }

    let lastFrameUrl = prevLastFrameUrl;

    // If we don't have the last frame URL cached, try to get it from task
    if (!lastFrameUrl && prevTaskId) {
      try {
        const taskR = await fetch(BASE + '/api/v1/tasks/' + prevTaskId, {headers:{'X-API-Key':getKey()}});
        if (taskR.ok) {
          const task = await taskR.json();
          if (task.last_frame_url) {
            lastFrameUrl = toLocalUrl(task.last_frame_url);
            window[`seg_${prevSegId}_last_frame_url`] = lastFrameUrl;
          }
        }
      } catch(e) {
        console.error('Failed to get task info:', e);
      }
    }

    if (!lastFrameUrl) {
      alert('无法获取上一段的最后一帧，请稍后重试');
      return;
    }

    // Fetch the last frame image and convert to base64
    try {
      const imgBlob = await fetch(lastFrameUrl).then(r => r.blob());
      const reader = new FileReader();
      imageBase64 = await new Promise((resolve) => {
        reader.onload = (e) => resolve(e.target.result.split(',')[1]);
        reader.readAsDataURL(imgBlob);
      });
    } catch(e) {
      alert('加载最后一帧失败: ' + e.message);
      return;
    }
  }

  const originalText = btn.textContent;
  btn.textContent = '描述中...';
  btn.disabled = true;

  try {
    const r = await fetch(BASE + '/api/v1/prompt/describe-image', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-API-Key': getKey()},
      body: JSON.stringify({image_base64: imageBase64})
    });

    if (!r.ok) {
      const err = await r.json();
      alert('描述失败: ' + (err.detail || JSON.stringify(err)));
      return;
    }

    const data = await r.json();
    if (data.description) {
      // Show description in preview area
      const descArea = document.getElementById(`seg-${segId}-description`);
      const descText = document.getElementById(`seg-${segId}-description-text`);
      descText.textContent = data.description;
      descArea.style.display = 'block';

      // Auto-expand preview area
      const previewArea = document.getElementById(`seg-${segId}-preview-area`);
      const toggleText = document.getElementById(`seg-${segId}-preview-toggle`);
      previewArea.style.display = 'block';
      toggleText.textContent = '▲ 隐藏参考图';
    }
  } catch(e) {
    alert('请求失败: ' + e.message);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

// Remove a segment
// Debug functions
function showDebugInfo() {
  const debugDiv = document.getElementById('debug-info');
  const savedSegments = localStorage.getItem('wan22_chain_segments');
  const savedCounter = localStorage.getItem('wan22_chain_counter');

  let html = '<div style="margin-top:8px">';
  html += `<div><b>Counter:</b> ${savedCounter || 'null'}</div>`;

  if (savedSegments) {
    try {
      const segments = JSON.parse(savedSegments);
      html += `<div><b>Segments count:</b> ${segments.length}</div>`;
      html += '<div style="margin-top:6px"><b>Segments data:</b></div>';
      segments.forEach((seg, i) => {
        html += `<div style="margin-left:12px;margin-top:4px">`;
        html += `<div>Segment ${i+1} (ID: ${seg.id}):</div>`;
        html += `<div style="margin-left:12px;color:#666">Prompt: ${seg.prompt ? seg.prompt.substring(0, 50) + '...' : 'empty'}</div>`;
        html += `<div style="margin-left:12px;color:#666">Duration: ${seg.duration}s</div>`;
        html += `<div style="margin-left:12px;color:#666">LoRAs: ${seg.loras ? seg.loras.length : 0}</div>`;
        html += `<div style="margin-left:12px;color:#666">Video: ${seg.videoUrl ? 'Yes' : 'No'}</div>`;
        html += `</div>`;
      });
    } catch(e) {
      html += `<div style="color:#f87171">Parse error: ${e.message}</div>`;
    }
  } else {
    html += '<div style="color:#888">No saved segments</div>';
  }

  html += '</div>';
  debugDiv.innerHTML = html;
}

function clearSegmentsStorage() {
  if (confirm('确定要清除所有保存的分段数据吗？')) {
    localStorage.removeItem('wan22_chain_segments');
    localStorage.removeItem('wan22_chain_counter');
    alert('已清除保存的数据，刷新页面后将重新开始');
    location.reload();
  }
}

// Get all segments data
function getSegmentsData() {
  const segments = [];
  document.querySelectorAll('.segment-card').forEach(card => {
    const id = card.dataset.segmentId;
    const promptEl = document.getElementById(`seg-${id}-prompt`);
    const durationEl = document.getElementById(`seg-${id}-duration`);
    if (!promptEl || !durationEl) return;
    const prompt = promptEl.value;
    const duration = parseFloat(durationEl.value);
    const loras = getSelectedLoras(`seg-${id}-loras`);

    if (prompt) {
      segments.push({
        prompt: prompt,
        duration: duration,
        loras: loras
      });
    }
  });
  return segments;
}

// Save segments to localStorage
function saveSegmentsToStorage() {
  const segments = [];
  document.querySelectorAll('.segment-card').forEach(card => {
    const id = card.dataset.segmentId;
    const promptEl = document.getElementById(`seg-${id}-prompt`);
    const durationEl = document.getElementById(`seg-${id}-duration`);
    if (!promptEl || !durationEl) return;
    const prompt = promptEl.value;
    const duration = parseFloat(durationEl.value);
    const loras = getSelectedLoras(`seg-${id}-loras`);
    const videoUrl = window[`seg_${id}_video_url`];
    const lastFrameUrl = window[`seg_${id}_last_frame_url`];
    const taskId = window[`seg_${id}_task_id`];

    segments.push({
      id: id,
      prompt: prompt,
      duration: duration,
      loras: loras,
      videoUrl: videoUrl || null,
      lastFrameUrl: lastFrameUrl || null,
      taskId: taskId || null
    });
  });

  localStorage.setItem('wan22_chain_segments', JSON.stringify(segments));
  localStorage.setItem('wan22_chain_counter', segmentCounter);
  console.log('Saved segments to storage:', segments.length, 'segments');
}

// Restore segments from localStorage
function restoreSegmentsFromStorage() {
  try {
    const savedSegments = localStorage.getItem('wan22_chain_segments');
    const savedCounter = localStorage.getItem('wan22_chain_counter');

    console.log('Attempting to restore segments from storage...');
    console.log('Saved segments:', savedSegments);
    console.log('Saved counter:', savedCounter);

    if (!savedSegments) {
      console.log('No saved segments found');
      return false;
    }

    const segments = JSON.parse(savedSegments);
    if (!segments || segments.length === 0) {
      console.log('Segments array is empty');
      return false;
    }

    console.log('Restoring', segments.length, 'segments');

    // Restore counter
    if (savedCounter) {
      segmentCounter = parseInt(savedCounter);
    }

    // Clear existing segments
    const container = document.getElementById('segments-container');
    if (!container) {
      console.error('segments-container not found!');
      return false;
    }
    container.innerHTML = '';

    // Restore each segment
    segments.forEach((seg, index) => {
      console.log(`Restoring segment ${index + 1}:`, seg);

      const segmentDiv = document.createElement('div');
      segmentDiv.className = 'segment-card';
      segmentDiv.dataset.segmentId = seg.id;

      segmentDiv.innerHTML = `
        <div class="segment-header">
          <span class="segment-title">分段 ${seg.id}</span>
          <div style="display:flex;gap:8px">
            <button class="btn btn-sm" style="background:#0f3460;padding:4px 12px" onclick="generateSingleSegment(${seg.id})">单独生成</button>
            <button class="segment-remove" onclick="removeSegment(${seg.id})">删除</button>
          </div>
        </div>
        <div class="row">
          <div class="field" style="flex:2">
            <label>提示词</label>
            <textarea id="seg-${seg.id}-prompt" placeholder="描述这一段的内容..." style="min-height:60px">${seg.prompt || ''}</textarea>
            <div style="margin-top:6px;display:flex;gap:8px">
              <button class="btn btn-sm" style="background:#0f3460;padding:4px 12px" onclick="optimizeSegmentPrompt(${seg.id}, this)">AI 优化 Prompt</button>
              <button class="btn btn-sm" style="background:#0f3460;padding:4px 12px" onclick="recommendSegmentLoras(${seg.id}, this)">AI 推荐 LoRA</button>
            </div>
          </div>
          <div class="field" style="flex:0;min-width:100px">
            <label>时长(秒)</label>
            <input type="number" id="seg-${seg.id}-duration" value="${seg.duration}" step="0.1" min="0.5" max="10">
          </div>
        </div>
        <div style="margin-bottom:12px">
          <button class="btn btn-sm" style="background:#16213e;padding:6px 14px" onclick="toggleSegmentPreview(${seg.id})">
            <span id="seg-${seg.id}-preview-toggle">▼ 查看参考图</span>
          </button>
          <button class="btn btn-sm" style="background:#1a4a2e;padding:6px 14px;margin-left:8px" onclick="analyzeAndContinue(${seg.id}, this)">
            🔍 VLM 分析续写
          </button>
          <button class="btn btn-sm" style="background:#2e1a4a;padding:6px 14px;margin-left:8px" onclick="describeSegmentImage(${seg.id}, this)">
            📝 仅描述图片
          </button>
        </div>
        <div id="seg-${seg.id}-preview-area" style="display:none;margin-bottom:12px;padding:12px;background:#0a0a23;border:1px solid #444;border-radius:6px">
          <div style="color:#888;font-size:12px;margin-bottom:8px">参考图（上一段的最后一帧）</div>
          <div id="seg-${seg.id}-preview-content" style="color:#666;font-size:13px">暂无参考图</div>
          <div id="seg-${seg.id}-description" style="margin-top:12px;padding:10px;background:#16213e;border-radius:4px;display:none">
            <div style="color:#7c83ff;font-size:12px;margin-bottom:6px">VLM 图片描述：</div>
            <div id="seg-${seg.id}-description-text" style="color:#e0e0e0;font-size:13px;line-height:1.5"></div>
          </div>
        </div>
        <div class="lora-section">
          <label style="cursor:pointer;user-select:none" onclick="toggleSegLoras(${seg.id}, this)">▶ LoRA 选择（可选）</label>
          <div class="lora-grid" id="seg-${seg.id}-loras" style="display:none"></div>
        </div>
        <div id="seg-${seg.id}-status" style="margin-top:12px"></div>
      `;

      container.appendChild(segmentDiv);

      // Restore video URL and last frame URL if exists
      if (seg.videoUrl) {
        window[`seg_${seg.id}_video_url`] = seg.videoUrl;
        console.log(`Restored video URL for segment ${seg.id}`);

        // Display the video result
        const statusBox = document.getElementById(`seg-${seg.id}-status`);
        if (statusBox) {
          statusBox.innerHTML = `
            <div style="font-size:13px">
              <span class="status completed">完成</span>
              <span style="color:#888;margin-left:8px">已恢复</span>
            </div>
            <div class="video-result" style="margin-top:8px">
              <div id="video-placeholder-chain-seg-${seg.id}" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:16px;text-align:center;cursor:pointer" onclick="loadVideo('chain-seg-${seg.id}', '${seg.videoUrl}')">
                <div style="color:#7c83ff;font-size:14px;margin-bottom:6px">🎬 点击加载视频</div>
                <div style="font-size:11px;color:#888">避免自动加载</div>
              </div>
              <div style="margin-top:4px"><a href="${seg.videoUrl}" download style="color:#7c83ff;font-size:12px">下载视频</a></div>
            </div>
          `;
        }
      }

      // Restore last frame URL if exists
      if (seg.lastFrameUrl) {
        window[`seg_${seg.id}_last_frame_url`] = toLocalUrl(seg.lastFrameUrl);
        console.log(`Restored last frame URL for segment ${seg.id}`);
      }

      // Restore task ID if exists
      if (seg.taskId) {
        window[`seg_${seg.id}_task_id`] = seg.taskId;
        console.log(`Restored task ID for segment ${seg.id}`);
      }

      // Don't render LoRAs immediately - save pending selections for when user expands
      if (seg.loras && seg.loras.length > 0) {
        window['_pendingSegLoras_' + seg.id] = seg.loras;
      }
    });

    console.log(`Restored ${segments.length} segments from storage`);
    return true;
  } catch(e) {
    console.error('Failed to restore segments:', e);
    return false;
  }
}

async function submitChain() {
  // Get segments data
  const segments = getSegmentsData();
  if (segments.length === 0) {
    alert('请至少添加一个分段');
    return;
  }

  const seed = parseInt(document.getElementById('chain-seed').value);
  const imageMode = document.querySelector('input[name="chain-image-mode"]:checked').value;
  console.log('[submitChain] imageMode:', imageMode, '| chainSelectedFile:', chainSelectedFile?.name || null);

  const params = {
    segments: segments,
    negative_prompt: document.getElementById('chain-neg').value,
    model: document.getElementById('chain-model').value,
    model_preset: document.getElementById('chain-preset').value,
    t5_preset: document.getElementById('chain-t5preset').value,
    width: parseInt(document.getElementById('chain-w').value),
    height: parseInt(document.getElementById('chain-h').value),
    fps: parseInt(document.getElementById('chain-fps').value),
    steps: parseInt(document.getElementById('chain-steps').value),
    cfg: parseFloat(document.getElementById('chain-cfg').value),
    shift: parseFloat(document.getElementById('chain-shift').value),
    seed: seed >= 0 ? seed : null,
    scheduler: document.getElementById('chain-sched').value,
    noise_aug_strength: parseFloat(document.getElementById('chain-noise').value),
    motion_amplitude: parseFloat(document.getElementById('chain-motion').value),
    auto_lora: document.getElementById('chain-auto-lora').checked,
    auto_prompt: document.getElementById('chain-auto-prompt').checked,
    transition: document.getElementById('chain-transition').value,
    auto_continue: document.getElementById('chain-auto-continue').checked,
    color_match: document.getElementById('chain-colormatch').value === 'true',
    color_match_method: document.getElementById('chain-colormatch-method').value,
    resize_mode: document.getElementById('chain-resize').value,
    image_mode: imageMode,
    face_swap_strength: parseFloat(document.getElementById('chain-face-swap-strength').value),
    motion_frames: parseInt(document.getElementById('chain-story-motion-frames').value),
    boundary: parseFloat(document.getElementById('chain-story-boundary').value),
    clip_preset: document.getElementById('chain-story-clip').value,
    match_image_ratio: document.getElementById('chain-story-match-ratio').checked,
    enable_upscale: document.getElementById('chain-enable-upscale').checked,
    upscale_model: document.getElementById('chain-upscale-model').value,
    upscale_resize: document.getElementById('chain-upscale-resize').value,
    enable_interpolation: document.getElementById('chain-enable-interpolation').checked,
    interpolation_multiplier: parseInt(document.getElementById('chain-interpolation-multiplier').value),
    interpolation_profile: 'small',
    enable_mmaudio: document.getElementById('chain-enable-mmaudio').checked,
    mmaudio_prompt: document.getElementById('chain-mmaudio-prompt').value,
    mmaudio_negative_prompt: document.getElementById('chain-mmaudio-neg').value,
    mmaudio_steps: parseInt(document.getElementById('chain-mmaudio-steps').value),
    mmaudio_cfg: parseFloat(document.getElementById('chain-mmaudio-cfg').value),
  };
  saveFormParams('chain');
  const fd = new FormData();
  if (chainSelectedFile) {
    if (imageMode === 'first_frame') {
      fd.append('image', chainSelectedFile);
      console.log('[submitChain] appending image field (first_frame)');
    } else if (imageMode === 'face_reference') {
      fd.append('face_image', chainSelectedFile);
      console.log('[submitChain] appending face_image field (face_reference)');
    }
  } else {
    console.log('[submitChain] no file selected');
  }
  fd.append('params', JSON.stringify(params));
  const box = document.getElementById('chain-status');
  box.innerHTML = '<div style="color:#aaa">提交中...</div>';
  try {
    const r = await fetch(BASE + '/api/v1/generate/chain', {
      method: 'POST', headers: {'X-API-Key': getKey()}, body: fd
    });
    const d = await r.json();
    if (!r.ok) { box.innerHTML = `<div style="color:#f87171">${d.detail || JSON.stringify(d)}</div>`; return; }
    // Keep the image cached for reuse, don't clear it
    pollChain(d.chain_id, d.total_segments);
  } catch(e) { box.innerHTML = `<div style="color:#f87171">请求失败: ${e.message}</div>`; }
}

function pollChain(chainId, totalSegments) {
  const box = document.getElementById('chain-status');
  const poll = async () => {
    try {
      const r = await fetch(BASE + '/api/v1/chains/' + chainId, {headers:{'X-API-Key':getKey()}});
      if (!r.ok) { setTimeout(poll, 3000); return; }
      const d = await r.json();
      const total = d.total_segments || totalSegments;
      const completed = d.completed_segments || 0;
      const curProg = d.current_task_progress || 0;
      const overallPct = Math.round(((completed + curProg) / total) * 100);
      const statusMap = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败',partial:'部分完成'};
      const statusText = statusMap[d.status] || d.status;
      const statusCls = d.status === 'completed' ? 'completed' : d.status === 'failed' ? 'failed' : d.status === 'partial' ? 'failed' : 'running';

      // Fetch segment task details for timing info
      let segDetails = [];
      if (d.segment_task_ids && d.segment_task_ids.length) {
        try {
          const tasks = await Promise.all(d.segment_task_ids.map(id =>
            fetch(BASE + '/api/v1/tasks/' + id, {headers:{'X-API-Key':getKey()}}).then(r => r.ok ? r.json() : null)
          ));
          segDetails = tasks;
        } catch(e) {}
      }

      // Segment indicators with timing
      let segHtml = '<div style="display:flex;gap:4px;margin:8px 0;flex-wrap:wrap">';
      for (let i = 0; i < total; i++) {
        let color = '#333'; // pending
        let tooltip = `第${i+1}段`;
        if (i < completed) {
          color = '#4ade80'; // done
          if (segDetails[i] && segDetails[i].created_at && segDetails[i].completed_at) {
            const dur = segDetails[i].completed_at - segDetails[i].created_at;
            tooltip += ` (${dur}秒)`;
          }
        } else if (i === d.current_segment && d.status === 'running') {
          color = '#7c83ff'; // current
        }
        segHtml += `<div title="${tooltip}" style="width:24px;height:24px;border-radius:4px;background:${color};display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff">${i+1}</div>`;
      }
      segHtml += '</div>';

      // Segment timing details
      let timingHtml = '';
      if (segDetails.length > 0) {
        const timings = segDetails.map((t, i) => {
          if (!t) return null;
          if (t.status === 'completed' && t.created_at && t.completed_at) {
            const dur = t.completed_at - t.created_at;
            const min = Math.floor(dur / 60);
            const sec = dur % 60;
            return `第${i+1}段: ${min > 0 ? min + '分' : ''}${sec}秒`;
          } else if (t.status === 'running') {
            return `第${i+1}段: 生成中...`;
          } else if (t.status === 'failed') {
            return `第${i+1}段: 失败`;
          }
          return null;
        }).filter(Boolean);
        if (timings.length) {
          timingHtml = `<div style="font-size:12px;color:#aaa;margin-top:4px">${timings.join(' | ')}</div>`;
        }
        // Total time
        if (d.created_at && d.completed_at) {
          const totalDur = d.completed_at - d.created_at;
          const totalMin = Math.floor(totalDur / 60);
          const totalSec = totalDur % 60;
          timingHtml += `<div style="font-size:12px;color:#4ade80;margin-top:2px">总耗时: ${totalMin > 0 ? totalMin + '分' : ''}${totalSec}秒</div>`;
        }
      }

      const cancelBtn = (d.status === 'running' || d.status === 'queued') ? `<button class="btn btn-sm" style="margin-left:8px;background:#4a1a1a;font-size:11px;padding:3px 10px" onclick="cancelChain('${chainId}')">取消</button>` : '';
      let html = `<div class="task-card">
        <div class="task-header"><span><b>长视频</b> <span class="task-id">${chainId.substring(0,8)}</span>${cancelBtn}</span>
          <span class="status ${statusCls}">${statusText}</span></div>
        ${segHtml}
        <div class="progress-bar"><div class="progress-fill" style="width:${overallPct}%"></div></div>
        <div style="font-size:12px;color:#666">${overallPct}% (${completed}/${total} 段)</div>
        ${timingHtml}`;
      if (d.error) html += `<div style="color:#f87171;font-size:13px;margin-top:4px">${d.error}</div>`;
      if (d.final_video_url) html += `<div class="video-result" style="margin-top:8px"><video src="${d.final_video_url}" controls autoplay loop style="max-width:100%;max-height:400px;border-radius:6px"></video>
        <div style="margin-top:6px"><a href="${d.final_video_url}" download style="color:#7c83ff;font-size:13px">下载完整视频</a></div></div>`;
      // Show individual segment videos
      if (d.segment_task_ids && d.segment_task_ids.length) {
        html += `<div style="margin-top:8px;font-size:12px;color:#888">各段任务: ${d.segment_task_ids.map((id,i) => `<span style="color:#7c83ff;cursor:pointer" onclick="document.getElementById('query-id').value='${id}';document.querySelector('.tab[data-tab=query]').click();queryTask();">${i+1}</span>`).join(' ')}</div>`;
      }
      html += '</div>';
      box.innerHTML = html;
      if (d.status === 'completed' || d.status === 'failed' || d.status === 'partial') return;
    } catch(e) {}
    setTimeout(poll, 3000);
  };
  poll();
}

async function mergeSegments() {
  const allSegments = Array.from(document.querySelectorAll('.segment-card'));

  // Collect task IDs from segments that have videos
  const taskIds = [];
  for (const segment of allSegments) {
    const segId = segment.dataset.segmentId;
    const taskId = window[`seg_${segId}_task_id`];
    const videoUrl = window[`seg_${segId}_video_url`];

    if (taskId && videoUrl) {
      taskIds.push(taskId);
    }
  }

  if (taskIds.length < 2) {
    alert('至少需要 2 个已生成的视频段才能合并');
    return;
  }

  const container = document.getElementById('merged-video-container');
  container.innerHTML = '<div style="color:#7c83ff;font-size:13px">正在合并视频...</div>';

  try {
    const r = await fetch(BASE + '/api/v1/generate/merge-segments', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': getKey()
      },
      body: JSON.stringify({
        segment_task_ids: taskIds
      })
    });

    if (!r.ok) {
      const err = await r.json();
      container.innerHTML = `<div style="color:#f87171;font-size:13px">合并失败: ${err.detail || JSON.stringify(err)}</div>`;
      return;
    }

    const data = await r.json();
    container.innerHTML = `
      <div style="background:#0a0a23;border:1px solid #444;border-radius:8px;padding:16px;margin-top:12px">
        <div style="color:#4ade80;font-size:14px;margin-bottom:8px">✓ 成功合并 ${data.segment_count} 个视频段</div>
        <div class="video-result">
          <div id="video-placeholder-merged" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:20px;text-align:center;cursor:pointer" onclick="loadVideo('merged', '${data.video_url}')">
            <div style="color:#7c83ff;font-size:14px;margin-bottom:8px">🎬 点击加载合并视频</div>
            <div style="font-size:12px;color:#888">视频已合并完成</div>
          </div>
          <div style="margin-top:8px;display:flex;gap:12px">
            <a href="${data.video_url}" download style="color:#7c83ff;font-size:13px">下载合并视频</a>
            <button class="btn btn-sm" style="background:#4a1a1a;padding:4px 12px;font-size:12px" onclick="document.getElementById('merged-video-container').innerHTML=''">关闭</button>
          </div>
        </div>
      </div>
    `;
  } catch(e) {
    container.innerHTML = `<div style="color:#f87171;font-size:13px">请求失败: ${e.message}</div>`;
  }
}

// Story Workflow functions
async function loadWorkflowList() {
  try {
    const r = await fetch(BASE + '/api/v1/workflow/list', {
      headers: { 'X-API-Key': getKey() }
    });
    if (!r.ok) return;

    const data = await r.json();
    const select = document.getElementById('story-workflow');
    select.innerHTML = '<option value="">-- 选择 Workflow --</option>';

    for (const wf of data.workflows) {
      const opt = document.createElement('option');
      opt.value = wf.name;
      opt.textContent = `${wf.name} (${(wf.size / 1024).toFixed(1)} KB)`;
      select.appendChild(opt);
    }
  } catch(e) {
    console.error('Failed to load workflows:', e);
  }
}

async function submitStoryWorkflow() {
  const workflowName = 'WAN2.2-I2V-AutoPrompt-Story_api';

  if (!storySelectedFile) {
    alert('请上传首帧图片');
    return;
  }

  // Save form params
  saveFormParams('story');

  const fps = 16; // Story workflow fixed at 16fps
  const params = {
    shift: parseFloat(document.getElementById('story-shift').value),
    seed: parseInt(document.getElementById('story-seed').value),
    motion_amplitude: parseFloat(document.getElementById('story-motion').value),
    motion_frames: parseInt(document.getElementById('story-motion-frames').value),
  };

  // Collect per-segment prompts and durations
  const segInfo = [];
  for (let i = 1; i <= 4; i++) {
    const prompt = document.getElementById(`story-seg-${i}-prompt`).value.trim();
    const duration = parseFloat(document.getElementById(`story-seg-${i}-duration`).value);
    const frames = durationToFrames(duration, fps);
    if (prompt) {
      params[`prompt_${i}`] = prompt;
    }
    params[`duration_${i}`] = duration;
    segInfo.push(`${duration}s(${frames}帧)`);
  }

  // Use segment 1 prompt as the global prompt (for nodes that match on "prompt")
  const prompt1 = document.getElementById('story-seg-1-prompt').value.trim();
  if (prompt1) {
    params.prompt = prompt1;
  }

  const model = 'a14b'; // Story workflow uses a14b (MoE)
  const statusDiv = document.getElementById('story-status');
  const hasCustomPrompt = Object.keys(params).some(k => k.startsWith('prompt_'));
  statusDiv.innerHTML = `<div style="color:#7c83ff">正在提交任务... 各段: ${segInfo.join(' / ')}${hasCustomPrompt ? '' : ' (AI 自动生成 Prompt)'}</div>`;

  try {
    const fd = new FormData();
    fd.append('image', storySelectedFile);
    fd.append('workflow_name', workflowName);
    fd.append('params', JSON.stringify(params));
    fd.append('model', model);
    const r = await fetch(BASE + '/api/v1/workflow/run-with-image', {
      method: 'POST',
      headers: { 'X-API-Key': getKey() },
      body: fd
    });

    if (!r.ok) {
      const err = await r.json();
      statusDiv.innerHTML = `<div style="color:#f87171">提交失败: ${err.detail || JSON.stringify(err)}</div>`;
      return;
    }

    const data = await r.json();
    statusDiv.innerHTML = '';
    addTask(data.task_id, 'STORY', prompt1 || '(AI 自动生成)');
    pollTask(data.task_id);
  } catch(e) {
    statusDiv.innerHTML = `<div style="color:#f87171">请求失败: ${e.message}</div>`;
  }
}

// Story tab removed - functionality consolidated into chain tab

// ═══════════════════════════════════════════════════════════════
// TTS Functions
// ═══════════════════════════════════════════════════════════════
function randomTTSSeed() {
  document.getElementById('tts-seed').value = Math.floor(Math.random() * 99999);
}

async function generateTTS() {
  const text = document.getElementById('tts-text').value.trim();
  if (!text) { alert('请输入文字内容'); return; }
  const statusEl = document.getElementById('tts-status');
  const resultEl = document.getElementById('tts-result');
  statusEl.textContent = '生成中...';
  statusEl.style.color = '#f0c040';
  resultEl.style.display = 'none';
  try {
    const res = await fetch('/api/v1/tts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text: text,
        seed: parseInt(document.getElementById('tts-seed').value) || null,
        temperature: parseFloat(document.getElementById('tts-temperature').value) || 0.3,
        top_p: parseFloat(document.getElementById('tts-top-p').value) || 0.7,
        top_k: parseInt(document.getElementById('tts-top-k').value) || 20,
        speed: parseInt(document.getElementById('tts-speed').value) || 5,
        oral: parseInt(document.getElementById('tts-oral').value) || 0,
        laugh: parseInt(document.getElementById('tts-laugh').value) || 0,
        pause: parseInt(document.getElementById('tts-pause').value) || 3,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'TTS failed');
    document.getElementById('tts-audio').src = data.audio_file;
    document.getElementById('tts-duration').textContent = data.duration + 's';
    document.getElementById('tts-download').href = data.audio_file;
    document.getElementById('tts-download').textContent = data.filename;
    resultEl.style.display = 'block';
    statusEl.textContent = '生成完成';
    statusEl.style.color = '#4caf50';
  } catch (e) {
    statusEl.textContent = '失败: ' + e.message;
    statusEl.style.color = '#f44336';
  }
}

// ═══════════════════════════════════════════════════════════════
// Postprocess Functions
let ppSelectedFile = null;

document.getElementById('pp-file').addEventListener('change', function() {
  const file = this.files[0];
  if (!file) return;
  ppSelectedFile = file;
  document.getElementById('pp-upload-text').textContent = file.name + ' (' + (file.size / 1024 / 1024).toFixed(1) + 'MB)';
  // Clear task ID since user chose file
  document.getElementById('pp-task-id').value = '';
  // Show preview
  const preview = document.getElementById('pp-preview');
  const info = document.getElementById('pp-preview-info');
  const video = document.getElementById('pp-preview-video');
  info.textContent = '已选择: ' + file.name;
  info.style.color = '#4ade80';
  video.src = URL.createObjectURL(file);
  video.style.display = 'block';
  preview.style.display = 'block';
});

function goPostproc(taskId) {
  document.getElementById('pp-task-id').value = taskId;
  ppSelectedFile = null;
  document.getElementById('pp-upload-text').textContent = '点击或拖拽上传视频';
  document.getElementById('pp-file').value = '';
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  document.querySelector('.tab[data-tab="postproc"]').classList.add('active');
  document.getElementById('panel-postproc').classList.add('active');
  ppLoadPreview();
}

async function ppLoadPreview() {
  const tid = document.getElementById('pp-task-id').value.trim();
  if (!tid) { alert('请输入任务ID'); return; }
  ppSelectedFile = null;
  document.getElementById('pp-upload-text').textContent = '点击或拖拽上传视频';
  document.getElementById('pp-file').value = '';
  const preview = document.getElementById('pp-preview');
  const info = document.getElementById('pp-preview-info');
  const video = document.getElementById('pp-preview-video');
  info.textContent = '加载中...';
  preview.style.display = 'block';
  video.style.display = 'none';
  try {
    const r = await fetch(BASE + '/api/v1/tasks/' + tid, {headers:{'X-API-Key':getKey()}});
    if (!r.ok) { info.textContent = '任务未找到'; info.style.color = '#f87171'; return; }
    const d = await r.json();
    if (d.status !== 'completed') { info.textContent = '任务未完成 (状态: ' + d.status + ')'; info.style.color = '#f87171'; return; }
    if (!d.video_url) { info.textContent = '任务无视频输出'; info.style.color = '#f87171'; return; }
    info.textContent = '任务 ' + tid.substring(0,8) + ' - 已完成';
    info.style.color = '#4ade80';
    video.src = d.video_url;
    video.style.display = 'block';
  } catch(e) { info.textContent = '加载失败: ' + e.message; info.style.color = '#f87171'; }
}

async function ppPostprocess(endpoint, fields, label) {
  const tid = document.getElementById('pp-task-id').value.trim();
  if (!tid && !ppSelectedFile) { alert('请输入任务ID或上传视频文件'); return; }
  const statusEl = document.getElementById('pp-status');
  statusEl.innerHTML = `<div style="color:#f0c040;font-size:13px">${label} 提交中...</div>`;
  try {
    const fd = new FormData();
    if (ppSelectedFile) {
      fd.append('video', ppSelectedFile);
    }
    if (tid) {
      fd.append('task_id', tid);
    }
    for (const [k, v] of Object.entries(fields)) {
      fd.append(k, String(v));
    }
    const r = await fetch(BASE + '/api/v1/postprocess/' + endpoint, {
      method: 'POST',
      headers: {'X-API-Key': getKey()},
      body: fd,
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || JSON.stringify(d));
    const source = tid || ppSelectedFile.name;
    statusEl.innerHTML = `<div style="color:#4ade80;font-size:13px">${label} 任务已创建: <span style="color:#7c83ff;cursor:pointer" onclick="document.getElementById('query-id').value='${d.task_id}';document.querySelector('.tab[data-tab=query]').click();queryTask();">${d.task_id}</span></div>`;
    addTask(d.task_id, label, source);
  } catch(e) {
    statusEl.innerHTML = `<div style="color:#f87171;font-size:13px">${label} 失败: ${e.message}</div>`;
  }
}

function ppInterpolate() {
  ppPostprocess('interpolate', {
    multiplier: document.getElementById('pp-interp-mult').value,
    resolution_profile: document.getElementById('pp-interp-profile').value,
    fps: document.getElementById('pp-interp-fps').value,
  }, '插帧');
}

function ppUpscale() {
  ppPostprocess('upscale', {
    model: document.getElementById('pp-upscale-model').value,
    resize_to: document.getElementById('pp-upscale-resize').value,
    fps: document.getElementById('pp-upscale-fps').value,
  }, '超分');
}

function ppAudio() {
  ppPostprocess('audio', {
    prompt: document.getElementById('pp-audio-prompt').value,
    negative_prompt: document.getElementById('pp-audio-neg').value,
    steps: document.getElementById('pp-audio-steps').value,
    cfg: document.getElementById('pp-audio-cfg').value,
    fps: document.getElementById('pp-audio-fps').value,
  }, 'AI配音');
}

let ppImgSelectedFile = null;
(function() {
  const area = document.getElementById('pp-img-upload-area');
  const fileInput = document.getElementById('pp-img-file');
  if (!area) return;
  area.addEventListener('click', () => fileInput.click());
  area.addEventListener('dragover', e => { e.preventDefault(); area.style.borderColor='#7c83ff'; });
  area.addEventListener('dragleave', () => { area.style.borderColor=''; });
  area.addEventListener('drop', e => {
    e.preventDefault(); area.style.borderColor='';
    const f = e.dataTransfer.files[0];
    if (f && f.type.startsWith('image/')) { ppImgSelectedFile = f; document.getElementById('pp-img-upload-text').textContent = f.name; }
  });
  fileInput.addEventListener('change', () => {
    const f = fileInput.files[0];
    if (f) { ppImgSelectedFile = f; document.getElementById('pp-img-upload-text').textContent = f.name; }
  });
})();

async function ppUpscaleImage() {
  if (!ppImgSelectedFile) { alert('请先上传图片'); return; }
  const statusEl = document.getElementById('pp-status');
  statusEl.innerHTML = '<div style="color:#f0c040;font-size:13px">图片放大处理中，请稍候...</div>';
  document.getElementById('pp-img-preview').style.display = 'none';
  try {
    const fd = new FormData();
    fd.append('image', ppImgSelectedFile);
    fd.append('model', document.getElementById('pp-img-model').value);
    const r = await fetch(BASE + '/api/v1/postprocess/upscale-image', {
      method: 'POST',
      headers: {'X-API-Key': getKey()},
      body: fd,
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || JSON.stringify(d));
    const imgEl = document.getElementById('pp-img-result');
    imgEl.src = d.url + '?t=' + Date.now();
    document.getElementById('pp-img-preview').style.display = 'block';
    statusEl.innerHTML = '<div style="color:#4ade80;font-size:13px">图片放大完成！<a href="' + d.url + '" download style="color:#7c83ff;margin-left:8px">下载</a></div>';
  } catch(e) {
    statusEl.innerHTML = '<div style="color:#f87171;font-size:13px">图片放大失败: ' + e.message + '</div>';
  }
}

// T2V/I2V Face Swap Functions
function toggleT2VFaceSwap() {
  const checked = document.getElementById('t2v-faceswap').checked;
  document.getElementById('t2v-faceswap-panel').style.display = checked ? 'block' : 'none';
}

function toggleI2VFaceSwap() {
  const checked = document.getElementById('i2v-faceswap').checked;
  document.getElementById('i2v-faceswap-panel').style.display = checked ? 'block' : 'none';
}

function previewT2VFace(input) {
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = function(e) {
      const preview = document.getElementById('t2v-face-preview');
      preview.src = e.target.result;
      preview.style.display = 'block';
    };
    reader.readAsDataURL(input.files[0]);
  }
}

function previewI2VFace(input) {
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = function(e) {
      const preview = document.getElementById('i2v-face-preview');
      preview.src = e.target.result;
      preview.style.display = 'block';
    };
    reader.readAsDataURL(input.files[0]);
  }
}

// Smart Recommendation Modal
let recommendModal = null;
let currentRecommendMode = null;

function openRecommendModal(mode) {
  currentRecommendMode = mode;
  const prompt = document.getElementById(mode + '-prompt').value.trim();

  if (!prompt) {
    alert('请先输入提示词');
    return;
  }

  if (!recommendModal) {
    createRecommendModal();
  }

  recommendModal.style.display = 'flex';
  document.getElementById('recommend-loading').style.display = 'block';
  document.getElementById('recommend-content').style.display = 'none';

  fetchRecommendations(prompt, mode);
}

function createRecommendModal() {
  const modal = document.createElement('div');
  modal.id = 'recommend-modal';
  modal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:2000;align-items:center;justify-content:center';

  modal.innerHTML = `
    <div style="background:#16213e;border:1px solid #444;border-radius:12px;padding:24px;max-width:900px;width:90%;max-height:85vh;overflow-y:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
        <div style="font-size:20px;font-weight:600;color:#7c83ff">智能推荐</div>
        <button onclick="closeRecommendModal()" style="background:none;border:none;color:#aaa;font-size:28px;cursor:pointer;padding:0;width:32px;height:32px">×</button>
      </div>

      <div id="recommend-loading" style="text-align:center;padding:60px;color:#aaa">
        <div style="font-size:16px;margin-bottom:12px">正在分析...</div>
        <div style="font-size:13px;color:#666">语义搜索相似资源和LORA</div>
      </div>

      <div id="recommend-content" style="display:none">
        <div id="recommend-images-section" style="margin-bottom:24px">
          <div style="font-size:16px;font-weight:600;color:#e0e0e0;margin-bottom:12px;display:flex;align-items:center;gap:8px">
            <span>📷</span>
            <span>相似图片</span>
            <span id="recommend-images-count" style="font-size:13px;color:#666"></span>
          </div>
          <div id="recommend-images" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px"></div>
        </div>

        <div id="recommend-loras-section">
          <div style="font-size:16px;font-weight:600;color:#e0e0e0;margin-bottom:12px;display:flex;align-items:center;gap:8px">
            <span>🎬</span>
            <span>推荐LORA</span>
            <span id="recommend-loras-count" style="font-size:13px;color:#666"></span>
          </div>
          <div id="recommend-loras" style="display:flex;flex-direction:column;gap:8px"></div>
        </div>

        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:24px;padding-top:20px;border-top:1px solid #333">
          <button class="btn" style="background:#444" onclick="closeRecommendModal()">关闭</button>
          <button class="btn" onclick="applyRecommendations()">应用选中项</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(modal);
  recommendModal = modal;
}

async function fetchRecommendations(prompt, mode) {
  try {
    const apiKey = document.getElementById('apiKey').value;
    const res = await fetch('/api/v1/recommend', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': apiKey
      },
      body: JSON.stringify({
        prompt: prompt,
        mode: mode === 't2v' ? 'T2V' : 'I2V',
        include_images: true,
        include_loras: true,
        top_k_images: 6,
        top_k_loras: 8
      })
    });

    if (!res.ok) throw new Error('Failed to fetch recommendations');

    const data = await res.json();
    renderRecommendations(data);
  } catch (err) {
    console.error(err);
    document.getElementById('recommend-loading').innerHTML = '<div style="color:#f87171">推荐失败，请重试</div>';
  }
}

function renderRecommendations(data) {
  document.getElementById('recommend-loading').style.display = 'none';
  document.getElementById('recommend-content').style.display = 'block';

  // Render images
  const imagesContainer = document.getElementById('recommend-images');
  const imagesSection = document.getElementById('recommend-images-section');

  if (data.images && data.images.length > 0) {
    imagesSection.style.display = 'block';
    document.getElementById('recommend-images-count').textContent = `(${data.images.length})`;

    imagesContainer.innerHTML = data.images.map(img => `
      <div style="position:relative;cursor:pointer;border-radius:8px;overflow:hidden;border:2px solid transparent;transition:border-color 0.2s"
           onclick="selectRecommendImage(${img.resource_id}, this)"
           data-resource-id="${img.resource_id}"
           data-url="${img.url}">
        <img src="${img.url}" style="width:100%;height:120px;object-fit:cover;display:block">
        <div style="position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent,rgba(0,0,0,0.8));padding:6px 8px;font-size:11px;color:#4ade80">
          相似度: ${(img.similarity * 100).toFixed(1)}%
        </div>
        <div style="position:absolute;top:6px;right:6px;width:20px;height:20px;border-radius:50%;background:#16213e;border:2px solid #7c83ff;display:none" class="select-indicator"></div>
      </div>
    `).join('');
  } else {
    imagesSection.style.display = 'none';
  }

  // Render LORAs
  const lorasContainer = document.getElementById('recommend-loras');
  const lorasSection = document.getElementById('recommend-loras-section');

  if (data.loras && data.loras.length > 0) {
    lorasSection.style.display = 'block';
    document.getElementById('recommend-loras-count').textContent = `(${data.loras.length})`;

    lorasContainer.innerHTML = data.loras.map(lora => {
      const triggerWords = Array.isArray(lora.trigger_words) ? lora.trigger_words.join(', ') : '';
      return `
        <div style="background:#0a0a23;border:1px solid #333;border-radius:8px;padding:12px;display:flex;gap:12px;align-items:center">
          <input type="checkbox" checked data-lora-id="${lora.lora_id}" data-lora-name="${lora.name}"
                 data-trigger-words="${triggerWords}" style="width:18px;height:18px;accent-color:#7c83ff;flex-shrink:0">
          <div style="flex:1;min-width:0">
            <div style="font-size:14px;font-weight:600;color:#e0e0e0;margin-bottom:4px">${lora.name}</div>
            ${lora.description ? `<div style="font-size:12px;color:#aaa;margin-bottom:4px">${lora.description}</div>` : ''}
            ${triggerWords ? `<div style="font-size:11px;color:#7c83ff">触发词: ${triggerWords}</div>` : ''}
          </div>
          <div style="font-size:12px;color:#4ade80;white-space:nowrap">${(lora.similarity * 100).toFixed(1)}%</div>
        </div>
      `;
    }).join('');
  } else {
    lorasSection.style.display = 'none';
  }
}

let selectedImageUrl = null;

function selectRecommendImage(resourceId, element) {
  // Deselect all
  document.querySelectorAll('#recommend-images > div').forEach(el => {
    el.style.borderColor = 'transparent';
    el.querySelector('.select-indicator').style.display = 'none';
  });

  // Select this one
  element.style.borderColor = '#7c83ff';
  element.querySelector('.select-indicator').style.display = 'block';
  selectedImageUrl = element.dataset.url;
}

function applyRecommendations() {
  const mode = currentRecommendMode;

  // Apply selected image for I2V mode
  if (mode === 'i2v' && selectedImageUrl) {
    // Set image preview
    const preview = document.getElementById('i2v-img-preview');
    preview.src = selectedImageUrl;
    preview.style.display = 'block';
    document.getElementById('i2v-upload-text').style.display = 'none';

    // Store URL for submission
    window.selectedI2VImageUrl = selectedImageUrl;
  }

  // Apply selected LORAs
  const selectedLoras = [];
  document.querySelectorAll('#recommend-loras input[type=checkbox]:checked').forEach(cb => {
    selectedLoras.push({
      id: cb.dataset.loraId,
      name: cb.dataset.loraName,
      triggerWords: cb.dataset.triggerWords
    });
  });

  if (selectedLoras.length > 0) {
    // Add trigger words to prompt
    const promptField = document.getElementById(mode + '-prompt');
    let currentPrompt = promptField.value.trim();

    selectedLoras.forEach(lora => {
      if (lora.triggerWords) {
        const words = lora.triggerWords.split(',').map(w => w.trim()).filter(w => w);
        words.forEach(word => {
          if (!currentPrompt.toLowerCase().includes(word.toLowerCase())) {
            currentPrompt += (currentPrompt ? ', ' : '') + word;
          }
        });
      }
    });

    promptField.value = currentPrompt;

    // TODO: Auto-select LORAs in the LORA selection UI if needed
  }

  closeRecommendModal();
  alert('推荐已应用！');
}

function closeRecommendModal() {
  if (recommendModal) {
    recommendModal.style.display = 'none';
  }
  selectedImageUrl = null;
  currentRecommendMode = null;
}

// Image upload - store file in variable to avoid losing it
document.getElementById('i2v-file').addEventListener('change', function() {
  const file = this.files[0];
  if (!file) return;
  i2vSelectedFile = file;
  const reader = new FileReader();
  const preview = document.getElementById('i2v-img-preview');
  const text = document.getElementById('i2v-upload-text');
  reader.onload = e => { preview.src = e.target.result; preview.style.display = 'block'; text.style.display = 'none'; };
  reader.readAsDataURL(file);
});

// Chain image upload
document.getElementById('chain-file').addEventListener('change', function() {
  const file = this.files[0];
  if (!file) return;
  chainSelectedFile = file;
  const reader = new FileReader();
  const preview = document.getElementById('chain-img-preview');
  const text = document.getElementById('chain-upload-text');
  reader.onload = e => {
    preview.src = e.target.result;
    preview.style.display = 'block';
    text.style.display = 'none';
  };
  reader.readAsDataURL(file);
});

// Chain image mode radio buttons
document.querySelectorAll('input[name="chain-image-mode"]').forEach(radio => {
  radio.addEventListener('change', function() {
    const isFaceRef = this.value === 'face_reference';
    document.getElementById('chain-face-swap-strength-field').style.display = isFaceRef ? '' : 'none';

    // Re-render first segment's LoRAs to apply filtering
    const container = document.getElementById('segments-container');
    if (container) {
      const firstSegment = container.querySelector('[data-segment-id]');
      if (firstSegment) {
        const segId = firstSegment.dataset.segmentId;
        const loraContainer = document.getElementById(`seg-${segId}-loras`);
        if (loraContainer && loraContainer.children.length > 0) {
          renderLoras(`seg-${segId}-loras`);
        }
      }
    }
  });
});

// Chain face image upload (for face_reference mode) - removed, use chainSelectedFile directly

// Story image upload
document.getElementById('story-file').addEventListener('change', function() {
  const file = this.files[0];
  if (!file) return;
  storySelectedFile = file;
  const reader = new FileReader();
  const preview = document.getElementById('story-img-preview');
  const text = document.getElementById('story-upload-text');
  reader.onload = e => { preview.src = e.target.result; preview.style.display = 'block'; text.style.display = 'none'; };
  reader.readAsDataURL(file);
});

// ===== Module Lifecycle =====
function __init_video() {
  // Load presets and LoRAs (data is cached globally, renders to video containers)
  loadPresets();
  loadT5Presets();
  loadLoras();

  // Restore form params
  restoreFormParams('t2v');
  restoreFormParams('i2v');
  restoreFormParams('chain');

  // Initialize story mode and post-processing UI
  if (typeof toggleStoryMode === 'function') toggleStoryMode(false);
  if (typeof togglePostProc === 'function') togglePostProc();

  // Auto-save chain form params on change
  FORM_FIELDS.chain.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('change', () => saveFormParams('chain'));
      el.addEventListener('input', () => saveFormParams('chain'));
    }
  });
  CHECKBOX_FIELDS.chain.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => saveFormParams('chain'));
  });
  document.querySelectorAll('input[name="chain-image-mode"]').forEach(radio => {
    radio.addEventListener('change', () => saveFormParams('chain'));
  });

  // Restore cached chain image from localStorage
  try {
    const cachedImg = localStorage.getItem('wan22_chain_image');
    const cachedName = localStorage.getItem('wan22_chain_image_name');
    if (cachedImg) {
      const preview = document.getElementById('chain-img-preview');
      const text = document.getElementById('chain-upload-text');
      if (preview && text) {
        preview.src = cachedImg;
        preview.style.display = 'block';
        text.style.display = 'none';
        fetch(cachedImg).then(r => r.blob()).then(blob => {
          chainSelectedFile = new File([blob], cachedName || 'cached.png', {type: blob.type});
        });
      }
    }
  } catch(e) {}
}

ModuleLoader.registerCleanup('video', () => {
  // Save form state before unloading
  try {
    saveFormParams('t2v');
    saveFormParams('i2v');
    saveFormParams('chain');
  } catch(e) {}
});
