// ===== Global State =====
const BASE = window.location.origin;
const getKey = () => document.getElementById('apiKey').value;

// Restore API key from localStorage on page load
(function() {
  const saved = localStorage.getItem('wan22_api_key');
  if (saved) document.getElementById('apiKey').value = saved;
})();

let allLoras = [];
let activeTasks = {};

// Annotate section variables (declared early to avoid initialization errors)
let annotatePage = 1;
let annotateTotalPages = 1;
const annotatePageSize = 30;
let currentAnnotateType = 'all';
let annotateCache = {};

// File selection state (shared across modules)
let i2vSelectedFile = null;
let storySelectedFile = null;
let chainSelectedFile = null;
let segmentCounter = 0;
let modelPresets = [];
let t5Presets = [];

// ===== Lazy media loader with concurrency control =====
// Videos: set src but keep preload="none" — only loads on user play click.
// Images: queue with concurrency limit to avoid overwhelming the server.
const _lazyQueue = [];
let _lazyActive = 0;
const _lazyMaxConcurrent = 3;

function _lazyLoadEl(el) {
  if (el.tagName === 'VIDEO') {
    // Just assign src; preload stays "none" — no network request until play
    el.src = el.dataset.src;
    el.dataset.loaded = 'true';
    return;
  }
  // Images: load with concurrency tracking
  _lazyActive++;
  el.src = el.dataset.src;
  el.dataset.loaded = 'true';
  const done = () => { _lazyActive--; _lazyFlush(); };
  el.onload = el.onerror = done;
}

function _lazyFlush() {
  while (_lazyQueue.length > 0 && _lazyActive < _lazyMaxConcurrent) {
    const el = _lazyQueue.shift();
    if (!el.dataset.loaded && el.dataset.src) {
      _lazyLoadEl(el);
    }
  }
}

let videoObserver = null;
function initVideoObserver() {
  if (!videoObserver) {
    videoObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const el = entry.target;
          videoObserver.unobserve(el);
          if (!el.dataset.loaded && el.dataset.src) {
            if (el.tagName === 'VIDEO') {
              // Videos: assign src immediately (preload="none" = no network request)
              _lazyLoadEl(el);
            } else {
              _lazyQueue.push(el);
            }
          }
        }
      });
      _lazyFlush();
    }, { rootMargin: '200px' });
  }
  return videoObserver;
}
function getVideoObserver() { return initVideoObserver(); }

/**
 * Observe all [data-src] media elements inside a container for lazy loading.
 * Call this after injecting HTML that uses data-src instead of src.
 */
function observeLazyMedia(container) {
  if (!container) return;
  const obs = initVideoObserver();
  container.querySelectorAll('[data-src]:not([data-loaded])').forEach(el => obs.observe(el));
}

// ===== URL Utilities =====
function toLocalUrl(url) {
  if (!url) return url;
  if (url.startsWith('/')) return url;
  if (url.startsWith('http://')) {
    url = url.replace('http://', 'https://');
  }
  try {
    const u = new URL(url);
    if (u.hostname.includes('.cos.')) {
      const filename = u.pathname.split('/').pop();
      return '/api/v1/results/' + filename;
    }
  } catch(e) {}
  return url;
}

const PROXY_HOSTS = ['image.civitai.com', 'cdn.imagime.co', 'imagime.co'];
function toProxyUrl(url) {
  if (!url || url.startsWith('/')) return url;
  try {
    const u = new URL(url);
    if (PROXY_HOSTS.some(h => u.hostname === h || u.hostname.endsWith('.' + h))) {
      return '/api/v1/proxy-media?url=' + encodeURIComponent(url);
    }
  } catch(e) {}
  return url;
}

function toThumbnailUrl(url, width = 400, height = 400) {
  if (!url || typeof url !== 'string') return url;
  const isImage = /\.(jpg|jpeg|png|webp|gif)$/i.test(url) || url.includes('/original') || url.includes('image-');
  if (!isImage) return url;
  if (url.includes('civitai.com')) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}width=${width}`;
  }
  if (url.includes('.cos.') || url.includes('.myqcloud.com')) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}imageMogr2/thumbnail/${width}x${height}`;
  }
  if (url.includes('imagime.co')) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}imageMogr2/thumbnail/${width}x${height}`;
  }
  return url;
}

function getVideoThumbnail(videoUrl) {
  return '';
}

// ===== Duration / Alignment Utilities =====
function formatDuration(created, completed) {
  if (!created) return '';
  const end = completed || Math.floor(Date.now()/1000);
  const sec = end - created;
  if (sec < 0) return '';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? m + '分' + s + '秒' : s + '秒';
}

function align16(el) {
  let v = parseInt(el.value) || 0;
  v = Math.round(v / 16) * 16;
  if (v < 16) v = 16;
  el.value = v;
}

function durationToFrames(sec, fps) {
  let f = Math.round(sec * fps);
  f = Math.max(f, 1);
  f = Math.round((f - 1) / 4) * 4 + 1;
  return Math.max(f, 5);
}

function framesToDuration(frames, fps) {
  return (frames / fps).toFixed(1);
}

// ===== Form Save / Restore =====
const FORM_FIELDS = {
  t2v: ['t2v-prompt','t2v-neg','t2v-model','t2v-preset','t2v-t5preset','t2v-w','t2v-h','t2v-duration','t2v-fps','t2v-steps','t2v-cfg','t2v-shift','t2v-seed','t2v-sched'],
  i2v: ['i2v-prompt','i2v-neg','i2v-model','i2v-preset','i2v-t5preset','i2v-w','i2v-h','i2v-duration','i2v-fps','i2v-steps','i2v-cfg','i2v-shift','i2v-seed','i2v-sched','i2v-noise','i2v-motion','i2v-colormatch','i2v-colormatch-method','i2v-resize'],
  chain: ['chain-prompt','chain-neg','chain-model','chain-preset','chain-t5preset','chain-w','chain-h','chain-total','chain-seg','chain-fps','chain-steps','chain-cfg','chain-shift','chain-seed','chain-sched','chain-noise','chain-motion','chain-transition','chain-colormatch','chain-colormatch-method','chain-resize','chain-story-motion-frames','chain-story-boundary','chain-story-clip','chain-upscale-resize','chain-interpolation-multiplier','chain-mmaudio-prompt','chain-mmaudio-neg','chain-mmaudio-steps','chain-mmaudio-cfg','chain-face-swap-strength'],
  story: ['story-shift','story-motion','story-motion-frames','story-seed','story-seg-1-prompt','story-seg-1-duration','story-seg-2-prompt','story-seg-2-duration','story-seg-3-prompt','story-seg-3-duration','story-seg-4-prompt','story-seg-4-duration']
};
const CHECKBOX_FIELDS = {
  t2v: ['t2v-auto-lora','t2v-auto-prompt'],
  i2v: ['i2v-auto-lora','i2v-auto-prompt'],
  chain: ['chain-auto-lora','chain-auto-prompt','chain-auto-continue','chain-story-match-ratio','chain-enable-upscale','chain-enable-interpolation','chain-enable-mmaudio'],
  story: []
};

function saveFormParams(prefix) {
  const data = {};
  FORM_FIELDS[prefix].forEach(id => {
    const el = document.getElementById(id);
    if (el) data[id] = el.value;
  });
  (CHECKBOX_FIELDS[prefix]||[]).forEach(id => {
    const el = document.getElementById(id);
    if (el) data[id] = el.checked;
  });
  if (prefix === 'chain') {
    const imageMode = document.querySelector('input[name="chain-image-mode"]:checked');
    if (imageMode) data['chain-image-mode'] = imageMode.value;
  }
  const loraData = [];
  document.querySelectorAll('#' + prefix + '-loras .lora-item').forEach(item => {
    const cb = item.querySelector('input[type=checkbox]');
    const idx = parseInt(cb.dataset.idx);
    const strength = parseFloat(item.querySelector('.lora-strength').value);
    if (cb.checked) loraData.push({idx, strength});
  });
  data._loras = loraData;
  localStorage.setItem('wan22_'+prefix, JSON.stringify(data));
}

function restoreFormParams(prefix) {
  try {
    const data = JSON.parse(localStorage.getItem('wan22_'+prefix));
    if (!data) return;
    FORM_FIELDS[prefix].forEach(id => {
      const el = document.getElementById(id);
      if (el && data[id] !== undefined) el.value = data[id];
    });
    (CHECKBOX_FIELDS[prefix]||[]).forEach(id => {
      const el = document.getElementById(id);
      if (el && data[id] !== undefined) el.checked = data[id];
    });
    if (prefix === 'chain' && data['chain-image-mode']) {
      const radio = document.querySelector(`input[name="chain-image-mode"][value="${data['chain-image-mode']}"]`);
      if (radio) {
        radio.checked = true;
        const event = new Event('change');
        radio.dispatchEvent(event);
      }
    }
    if (data._loras) {
      window['_pendingLoras_'+prefix] = data._loras;
    }
  } catch(e) {}
}

// ===== Presets =====
async function loadPresets() {
  try {
    const r = await fetch(BASE + '/api/v1/model-presets');
    if (!r.ok) return;
    modelPresets = await r.json();
    ['t2v-preset','i2v-preset','chain-preset'].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      sel.innerHTML = modelPresets.map(p =>
        `<option value="${p.name}" ${!p.available?'disabled':''}>${p.name}${p.available?'':' (未下载)'}</option>`
      ).join('');
    });
    ['t2v','i2v','chain'].forEach(prefix => {
      try {
        const data = JSON.parse(localStorage.getItem('wan22_'+prefix));
        if (data && data[prefix+'-preset']) {
          const el = document.getElementById(prefix+'-preset');
          if (el) el.value = data[prefix+'-preset'];
        }
      } catch(e) {}
    });
  } catch(e) {}
}

async function loadT5Presets() {
  try {
    const r = await fetch(BASE + '/api/v1/t5-presets');
    if (!r.ok) return;
    t5Presets = await r.json();
    ['t2v-t5preset','i2v-t5preset','chain-t5preset'].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      sel.innerHTML = '<option value="">默认</option>' + t5Presets.filter(p => p.name !== 'default').map(p =>
        `<option value="${p.name}" ${!p.available?'disabled':''}>${p.name}${p.available?'':' (未下载)'}</option>`
      ).join('');
    });
    ['t2v','i2v','chain'].forEach(prefix => {
      try {
        const data = JSON.parse(localStorage.getItem('wan22_'+prefix));
        if (data && data[prefix+'-t5preset']) {
          const el = document.getElementById(prefix+'-t5preset');
          if (el) el.value = data[prefix+'-t5preset'];
        }
      } catch(e) {}
    });
  } catch(e) {}
}

function onPresetChange(prefix) {
  const presetName = document.getElementById(prefix + '-preset').value;
  const preset = modelPresets.find(p => p.name === presetName);
  if (!preset || !preset.recommended_params) return;
  const rec = preset.recommended_params;
  if (rec.steps !== undefined) document.getElementById(prefix + '-steps').value = rec.steps;
  if (rec.cfg !== undefined) document.getElementById(prefix + '-cfg').value = rec.cfg;
  if (rec.scheduler !== undefined) document.getElementById(prefix + '-sched').value = rec.scheduler;
}

// ===== LoRA System =====
async function loadLoras() {
  try {
    const cached = localStorage.getItem('wan22_loras_cache');
    if (cached) {
      try { allLoras = JSON.parse(cached); } catch(e) {}
    }
    const r = await fetch(BASE + '/api/v1/loras', {headers: {'X-API-Key': getKey()}});
    if (!r.ok) return;
    const lorasResp = await r.json();
    allLoras = lorasResp.loras || lorasResp;
    localStorage.setItem('wan22_loras_cache', JSON.stringify(allLoras));
    if (document.getElementById('t2v-loras')) renderLoras('t2v-loras');
    if (document.getElementById('i2v-loras')) renderLoras('i2v-loras');
    restoreLoraSelections('t2v');
    restoreLoraSelections('i2v');
    restoreLoraSelections('chain');

    // Try to restore segments from storage first
    try {
      const savedSegments = localStorage.getItem('wan22_chain_segments');
      if (savedSegments) {
        const segments = JSON.parse(savedSegments);
        if (segments && segments.length > 0) {
          if (typeof restoreSegmentsFromStorage === 'function') {
            const restored = restoreSegmentsFromStorage();
            if (restored) return;
          }
        }
      }
    } catch(e) {}

    // Initialize chain panel with one default segment if no saved data
    const segContainer = document.getElementById('segments-container');
    if (segContainer && segContainer.children.length === 0 && typeof addSegment === 'function') {
      addSegment();
    }
  } catch(e) {
    console.error('loadLoras failed:', e);
  }
}

function restoreLoraSelections(prefix) {
  const pending = window['_pendingLoras_'+prefix];
  if (!pending) return;
  delete window['_pendingLoras_'+prefix];
  const container = prefix + '-loras';
  pending.forEach(({idx, strength}) => {
    const item = document.getElementById(container + '-' + idx);
    if (!item) return;
    const cb = item.querySelector('input[type=checkbox]');
    cb.checked = true;
    item.classList.add('selected');
    item.querySelector('.lora-strength').value = strength;
  });
}

function _isI2VLora(l) {
  const n = (l.name || '').toLowerCase();
  const f = (l.file || '').toLowerCase();
  if (n.includes('t2v') || f.includes('t2v')) return false;
  return n.includes('i2v') || f.includes('i2v') || f.includes('_high_noise') || f.includes('_low_noise');
}

function _isT2VLora(l) {
  const n = (l.name || '').toLowerCase();
  const f = (l.file || '').toLowerCase();
  if (n.includes('t2v') || f.includes('t2v')) return true;
  if (n.includes('i2v') || f.includes('i2v') || f.includes('_high_noise') || f.includes('_low_noise')) return false;
  return true;
}

function renderLoras(cid) {
  const c = document.getElementById(cid);
  if (!c) return;
  if (!allLoras.length) { c.innerHTML = '<span style="color:#666;font-size:13px">暂无可用 LoRA</span>'; return; }

  const isT2V = cid.startsWith('t2v-');
  let isChainSeg0 = false;
  if (cid.startsWith('seg-') && cid.endsWith('-loras')) {
    const match = cid.match(/^seg-(\d+)-loras$/);
    if (match) {
      const segId = match[1];
      const container = document.getElementById('segments-container');
      if (container) {
        const firstSegment = container.querySelector('[data-segment-id]');
        if (firstSegment && firstSegment.dataset.segmentId === segId) {
          isChainSeg0 = true;
        }
      }
    }
  }

  let filterForT2V = isT2V;
  if (isChainSeg0) {
    const imageMode = document.querySelector('input[name="chain-image-mode"]:checked')?.value;
    filterForT2V = (imageMode === 'face_reference');
  }

  c.innerHTML = allLoras.map((l, i) => {
    if (filterForT2V && !_isT2VLora(l)) return '';
    if (isT2V && _isI2VLora(l)) return '';

    const twText = (l.trigger_words||[]).join(', ');
    const isI2V = _isI2VLora(l);
    let previewHtml = '';
    if (l.preview_url) {
      const isVideo = /\.(mp4|webm)$/i.test(l.preview_url);
      if (isVideo) {
        const playIcon = `<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>`;
        previewHtml = `<div class="lora-preview-wrap" onclick="event.stopPropagation();var v=this.querySelector('video');if(v.paused){v.play();this.classList.add('playing')}else{v.pause();this.classList.remove('playing')}" ondblclick="event.stopPropagation();openPreviewModal('${l.preview_url}',true)"><video class="lora-preview" data-src="${l.preview_url}" muted loop playsinline preload="none" referrerpolicy="no-referrer"></video><div class="lora-play-btn">${playIcon}</div></div>`;
      } else {
        const thumbUrl = toThumbnailUrl(l.preview_url);
        previewHtml = `<img class="lora-preview" data-src="${thumbUrl}" alt="" referrerpolicy="no-referrer" onclick="openPreviewModal('${l.preview_url}', false)">`;
      }
    } else {
      previewHtml = '<div class="lora-placeholder">LoRA</div>';
    }
    const typeTag = isI2V ? '<span class="lora-type-tag i2v">I2V</span>' : '<span class="lora-type-tag t2v">T2V</span>';
    const civitaiLink = l.civitai_id ? `<a href="https://civitai.com/models/${l.civitai_id}" target="_blank" rel="noopener" title="${l.file}">${l.name}</a>` : `<span title="${l.file}">${l.name}</span>`;
    return `<div class="lora-item" id="${cid}-${i}">
      <input type="checkbox" data-idx="${i}" onchange="this.parentElement.classList.toggle('selected')">
      ${previewHtml}
      <div style="flex:1;min-width:0">
        <span class="lora-name">${civitaiLink}${typeTag}</span>
        ${twText ? `<div class="lora-tw" title="${twText}">${twText}</div>` : ''}
      </div>
      <input type="number" class="lora-strength" min="0" max="2" step="0.05" value="${l.default_strength}">
    </div>`;
  }).join('');
  // Activate lazy loading for media previews
  observeLazyMedia(c);
}

function getSelectedLoras(cid) {
  const loras = [];
  document.querySelectorAll('#' + cid + ' .lora-item').forEach(item => {
    const cb = item.querySelector('input[type=checkbox]');
    if (cb.checked) {
      const idx = parseInt(cb.dataset.idx);
      loras.push({name: allLoras[idx].name, strength: parseFloat(item.querySelector('.lora-strength').value)});
    }
  });
  return loras;
}

// ===== Task Management (with pollTask dedup fix) =====
const _pollingTasks = new Set(); // Track task IDs currently being polled

function addTask(taskId, type, prompt) {
  activeTasks[taskId] = {type, prompt};
  renderTasks();
  pollTask(taskId);
}

function _buildTaskCard(id, t) {
  const pct = Math.round((t.progress || 0) * 100);
  const statusCls = (t.status || 'queued').toLowerCase();
  const statusText = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败'}[statusCls] || statusCls;
  let videoHtml = '';
  if (t.video_url) {
    videoHtml = `<div class="video-result">
      <div id="video-placeholder-active-${id}" style="background:#0a0a23;border:1px solid #444;border-radius:6px;padding:16px;text-align:center;cursor:pointer" onclick="loadVideo('active-${id}', '${t.video_url}')">
        <div style="color:#7c83ff;font-size:14px;margin-bottom:6px">🎬 点击加载视频</div>
        <div style="font-size:11px;color:#888">避免自动加载，节省带宽</div>
      </div>
      <div style="margin-top:6px"><a href="${t.video_url}" download style="color:#7c83ff;font-size:13px">下载视频</a></div></div>`;
  }
  let errorHtml = t.error ? `<div style="color:#f87171;font-size:13px;margin-top:4px">${t.error}</div>` : '';
  const promptText = (t.prompt||'');
  const durStr = t.created_at ? formatDuration(t.created_at, t.completed_at) : '';
  let aiHtml = '';
  if (t.params) {
    if (t.params.ai_loras && t.params.ai_loras.length) {
      aiHtml += `<div style="font-size:11px;color:#7c83ff;margin-bottom:2px">AI LoRA: ${t.params.ai_loras.map(l=>l.name+'('+l.strength+')').join(', ')}</div>`;
    }
    const displayPrompt = t.params.final_prompt || t.params.ai_prompt;
    if (displayPrompt) {
      aiHtml += `<div style="font-size:11px;color:#7c83ff;margin-bottom:2px;white-space:pre-wrap">AI Prompt: ${displayPrompt}</div>`;
    }
  }
  const cancelBtn = (statusCls==='queued'||statusCls==='running') ? `<button class="btn btn-sm" style="margin-left:8px;background:#4a1a1a;font-size:11px;padding:3px 10px" onclick="cancelTask('${id}')">取消</button>` : '';
  const extendBtn = statusCls==='completed' && t.video_url ? `<button class="btn btn-sm" style="margin-left:8px;background:#1a4a2e;font-size:11px;padding:3px 10px" onclick="extendTask('${id}')">延续</button>` : '';
  const ppBtn = statusCls==='completed' && t.video_url ? `<button class="btn btn-sm" style="margin-left:8px;background:#2a1a4a;font-size:11px;padding:3px 10px" onclick="goPostproc('${id}')">后期处理</button>` : '';
  return `<div class="task-card" data-task-id="${id}">
    <div class="task-header"><span><b>${t.type||''}</b> <span class="task-id">${id}</span>${durStr ? ` <span data-role="dur" style="color:#4ade80;font-size:11px">⏱${durStr}</span>` : ` <span data-role="dur" style="color:#4ade80;font-size:11px"></span>`}${cancelBtn}${extendBtn}${ppBtn}</span>
      <span class="status ${statusCls}">${statusText}</span></div>
    <div style="font-size:13px;color:#aaa;margin-bottom:6px">${promptText}</div>
    ${aiHtml}
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div style="font-size:12px;color:#666" data-role="pct">${pct}%</div>
    ${errorHtml}${videoHtml}</div>`;
}

function renderTasks() {
  const list = document.getElementById('task-list');
  if (!list) return;
  const ids = Object.keys(activeTasks).reverse();
  if (!ids.length) { list.innerHTML = '<p style="color:#666;font-size:13px">暂无任务</p>'; return; }
  const existing = list.querySelectorAll('.task-card[data-task-id]');
  const existingIds = new Set(Array.from(existing).map(el => el.dataset.taskId));
  if (existing.length !== ids.length || ids.some(id => !existingIds.has(id))) {
    list.innerHTML = ids.map(id => _buildTaskCard(id, activeTasks[id])).join('');
    return;
  }
  ids.forEach(id => {
    const t = activeTasks[id];
    const card = list.querySelector(`.task-card[data-task-id="${id}"]`);
    if (!card) return;
    const pct = Math.round((t.progress || 0) * 100);
    const statusEl = card.querySelector('.status');
    const curStatus = statusEl ? statusEl.textContent : '';
    const statusText = {queued:'排队中',running:'生成中',completed:'完成',failed:'失败'}[(t.status||'queued').toLowerCase()] || t.status;
    if (curStatus !== statusText || (t.video_url && !card.querySelector('.video-result')) || (t.error && !card.querySelector('[style*="f87171"]'))) {
      card.outerHTML = _buildTaskCard(id, t);
      return;
    }
    const fill = card.querySelector('.progress-fill');
    if (fill) fill.style.width = pct + '%';
    const pctEl = card.querySelector('[data-role="pct"]');
    if (pctEl) pctEl.textContent = pct + '%';
    const durEl = card.querySelector('[data-role="dur"]');
    if (durEl && t.created_at) durEl.textContent = '⏱' + formatDuration(t.created_at, t.completed_at);
  });
}

async function cancelTask(taskId) {
  if (!confirm('确定取消此任务？')) return;
  try {
    const r = await fetch(BASE + '/api/v1/tasks/' + taskId + '/cancel', {
      method: 'POST', headers: {'X-API-Key': getKey()}
    });
    if (r.ok) {
      activeTasks[taskId] = {...activeTasks[taskId], status:'failed', error:'Cancelled by user'};
      renderTasks();
    } else {
      const d = await r.json();
      alert(d.detail || '取消失败');
    }
  } catch(e) { alert('取消失败: ' + e.message); }
}

async function cancelChain(chainId) {
  if (!confirm('确定取消此长视频任务？')) return;
  try {
    const r = await fetch(BASE + '/api/v1/chains/' + chainId + '/cancel', {
      method: 'POST', headers: {'X-API-Key': getKey()}
    });
    if (r.ok) {
      const box = document.getElementById('chain-status');
      if (box) box.innerHTML = '<div style="color:#f87171;font-size:13px">已取消</div>';
    } else {
      const d = await r.json();
      alert(d.detail || '取消失败');
    }
  } catch(e) { alert('取消失败: ' + e.message); }
}

// pollTask with dedup: prevents duplicate polling loops for the same taskId
async function pollTask(taskId) {
  if (_pollingTasks.has(taskId)) return; // Already polling this task
  _pollingTasks.add(taskId);
  const poll = async () => {
    try {
      const r = await fetch(BASE + '/api/v1/tasks/' + taskId, {headers:{'X-API-Key':getKey()}});
      if (!r.ok) { _pollingTasks.delete(taskId); return; }
      const d = await r.json();
      activeTasks[taskId] = {...activeTasks[taskId], ...d};
      renderTasks();
      if (d.status === 'completed' || d.status === 'failed') {
        _pollingTasks.delete(taskId);
        return;
      }
    } catch(e) { _pollingTasks.delete(taskId); return; }
    setTimeout(poll, 3000);
  };
  poll();
}

// ===== Video Loading =====
function loadVideo(id, url) {
  const placeholder = document.getElementById('video-placeholder-' + id);
  if (!placeholder) return;
  placeholder.innerHTML = '<div style="color:#7c83ff;font-size:13px">加载中...</div>';
  const video = document.createElement('video');
  video.src = url;
  video.controls = true;
  video.loop = true;
  video.autoplay = true;
  video.style.maxWidth = '100%';
  video.style.maxHeight = '300px';
  video.style.borderRadius = '6px';
  placeholder.replaceWith(video);
}

// ===== Preview Modal =====
function openPreviewModal(url, isVideo) {
  const modal = document.getElementById('preview-modal');
  const content = document.getElementById('preview-modal-content');
  if (isVideo) {
    content.innerHTML = `<button onclick="closePreviewModal()" style="position:absolute;top:-12px;right:-12px;background:#333;color:#fff;border:none;border-radius:50%;width:28px;height:28px;cursor:pointer;font-size:16px;z-index:1">×</button>
      <video src="${url}" controls autoplay loop style="max-width:90vw;max-height:85vh;border-radius:8px" referrerpolicy="no-referrer"></video>`;
  } else {
    content.innerHTML = `<button onclick="closePreviewModal()" style="position:absolute;top:-12px;right:-12px;background:#333;color:#fff;border:none;border-radius:50%;width:28px;height:28px;cursor:pointer;font-size:16px;z-index:1">×</button>
      <img src="${url}" style="max-width:90vw;max-height:85vh;border-radius:8px" referrerpolicy="no-referrer">`;
  }
  modal.style.display = 'flex';
}

function closePreviewModal() {
  const modal = document.getElementById('preview-modal');
  modal.style.display = 'none';
  const content = document.getElementById('preview-modal-content');
  const vid = content.querySelector('video');
  if (vid) vid.pause();
}

// ===== Prompt Optimization Modal =====
let _promptModalResolve = null;
let _promptModalOriginal = '';

function openPromptModal(original, optimized, explanation, triggers) {
  document.getElementById('modal-original').textContent = original;
  document.getElementById('modal-optimized').value = optimized;
  document.getElementById('modal-explanation').textContent = explanation || '';
  document.getElementById('modal-triggers').textContent = triggers.length ? 'Trigger words: ' + triggers.join(', ') : '';
  const modal = document.getElementById('prompt-modal');
  modal.style.display = 'flex';
  _promptModalOriginal = original;
  return new Promise(resolve => { _promptModalResolve = resolve; });
}

function closePromptModal(useOptimized) {
  document.getElementById('prompt-modal').style.display = 'none';
  if (_promptModalResolve) {
    _promptModalResolve(useOptimized ? document.getElementById('modal-optimized').value : _promptModalOriginal);
    _promptModalResolve = null;
  }
}

// ===== Duration Ticker (1s interval for running tasks) =====
setInterval(() => {
  const hasRunning = Object.values(activeTasks).some(t => t.status === 'running' || t.status === 'queued');
  if (!hasRunning) return;
  document.querySelectorAll('#task-list .task-card[data-task-id]').forEach(card => {
    const id = card.dataset.taskId;
    const t = activeTasks[id];
    if (!t || !t.created_at || t.completed_at) return;
    const durEl = card.querySelector('[data-role="dur"]');
    if (durEl) durEl.textContent = '⏱' + formatDuration(t.created_at, null);
  });
}, 1000);

// ===== Visibility Change Handler (with debounce fix) =====
let _visibilityDebounceTimer = null;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  if (_visibilityDebounceTimer) return; // Debounce: skip if already scheduled
  _visibilityDebounceTimer = setTimeout(() => {
    _visibilityDebounceTimer = null;
    Object.keys(activeTasks).forEach(id => {
      const t = activeTasks[id];
      if ((t.status === 'running' || t.status === 'queued') && !_pollingTasks.has(id)) {
        // Only fire immediate poll if not already being polled
        fetch(BASE + '/api/v1/tasks/' + id, {headers:{'X-API-Key':getKey()}})
          .then(r => r.ok ? r.json() : null)
          .then(d => { if (d) { activeTasks[id] = {...activeTasks[id], ...d}; renderTasks(); } })
          .catch(() => {});
      }
    });
  }, 500);
});
