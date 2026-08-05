const $ = id => document.getElementById(id);
const personaNameInput = $('personaName');
const tasteRoastTextarea = $('tasteRoast');
const specialNoteTextarea = $('specialNote');
const samplesTextarea = $('samples');
const resultBox = $('result');
const listBox = $('list');
const saveBtn = $('saveBtn');
const clearBtn = $('clearBtn');
const deleteCurrentBtn = $('deleteCurrentBtn');
const refreshBtn = $('refreshBtn');
const loadPersonaBtn = $('loadPersonaBtn');
const importPersonaNameInput = $('importPersonaName');
const importQQInput = $('importQQ');
const jsonFileInput = $('jsonFile');
const fileText = $('fileText');
const importBtn = $('importBtn');
const importResultBox = $('importResult');
const autoTagsStatusBox = $('autoTagsStatusBox');
const autoTagsResultBox = $('autoTagsResult');
const autoModelBadge = $('autoModelBadge');
const autoTagResultsBox = $('autoTagResultsBox');
const autoTagDetailBox = $('autoTagDetailBox');
const toast = $('toast');
const modal = $('personaModal');
const modalList = $('personaModalList');
const autoDownloadModal = $('autoDownloadModal');
const autoAnalyzeModal = $('autoAnalyzeModal');
const logsBox = $('logsBox');
const logLevel = $('logLevel');
const logQuery = $('logQuery');
const logAutoRefresh = $('logAutoRefresh');
let pendingForce = false;
let cachedPersonas = [];
let autoStatusTimer = null;
let autoStatusRequestActive = false;
let autoStatusRetryDelay = 3000;
let logsTimer = null;
let logsRequestActive = false;

function accessToken() {
  return new URLSearchParams(window.location.search).get('token') || '';
}

function api(path) {
  const url = new URL(path, window.location.href);
  const token = accessToken();
  if (token) url.searchParams.set('token', token);
  return url.pathname + url.search + url.hash;
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 2800);
}

function lines() {
  return samplesTextarea.value.split(String.fromCharCode(10)).map(s => s.trim()).filter(Boolean);
}

function setBox(box, ok, message) {
  box.className = 'result ' + (ok ? 'ok' : 'err');
  box.textContent = message;
  box.classList.remove('shake', 'pulse');
  void box.offsetWidth;
  box.classList.add(ok ? 'pulse' : 'shake');
  showToast(message);
}

function setResult(ok, message) {
  setBox(resultBox, ok, message);
}

function setImportResult(ok, message) {
  setBox(importResultBox, ok, message);
}

async function withLoading(el, fn) {
  el.classList.add('loading');
  el.disabled = true;
  try {
    return await fn();
  } finally {
    el.classList.remove('loading');
    el.disabled = false;
  }
}

async function jsonFetch(path, options) {
  try {
    const requestOptions = { ...(options || {}) };
    const headers = new Headers(requestOptions.headers || {});
    const token = accessToken();
    if (token) headers.set('X-Access-Token', token);
    requestOptions.headers = headers;
    const res = await fetch(api(path), requestOptions);
    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_error) {
      data = {};
    }
    if (!res.ok) {
      return { ok: false, message: data.message || ('请求失败（HTTP ' + res.status + '）') };
    }
    return data && typeof data === 'object' ? data : { ok: false, message: '接口返回格式不正确' };
  } catch (error) {
    return { ok: false, message: '请求失败：无法连接 WebUI 接口' };
  }
}

async function loadOverview() {
  const box = $('overviewCards');
  box.innerHTML = '';
  const data = await jsonFetch('/api/overview');
  if (!data.ok) {
    box.innerHTML = '<div class="empty">总览加载失败</div>';
    return;
  }
  const items = [['人格数量', data.persona_count], ['人格样本', data.sample_count], ['水鱼绑定', data.import_token_count], ['机台绑定', data.arcade_credential_count]];
  items.forEach(([label, value]) => {
    const card = document.createElement('div');
    card.className = 'stat';
    card.innerHTML = '<span></span><b></b>';
    card.querySelector('span').textContent = label;
    card.querySelector('b').textContent = value;
    box.appendChild(card);
  });
}

function setAutoTagsResult(ok, message) {
  if (!autoTagsResultBox) return;
  autoTagsResultBox.className = 'result ' + (ok ? 'ok' : 'err');
  autoTagsResultBox.textContent = message;
  showToast(message);
}

function dsRangeValue(value) {
  const parts = String(value || '').split('-').map(Number);
  if (parts.length !== 2 || parts.some(Number.isNaN)) return [10, 15];
  return parts;
}

function renderAutoTagsStatus(data) {
  if (!autoTagsStatusBox) return;
  const total = Number(data.catalog_total || 0);
  const analyzed = Number(data.catalog_analyzed || 0);
  const tagged = Number(data.catalog_tagged || 0);
  const percent = total ? Math.round(analyzed * 1000 / total) / 10 : 0;
  const taskTotal = Number(data.total ?? data.selected ?? 0);
  const processed = Number(data.processed || 0);
  const taskPercent = taskTotal ? Math.min(100, Math.round(processed * 1000 / taskTotal) / 10) : (data.running ? 0 : 100);
  const taskName = data.task === 'download' ? '下载谱面' : data.task === 'analysis' ? '谱面分析' : '暂无任务';
  const statusName = data.running ? '运行中' : ({ completed: '已完成', stopped: '已停止', failed: '失败' }[data.status] || '未运行');
  const modelLoaded = Boolean(data.model_metadata?.best_epoch || data.model_metadata?.model_type);
  autoModelBadge.textContent = modelLoaded ? '模型已加载' : data.model_error ? '模型不可用' : '模型待加载';
  const statusMessage = data.error || data.last_error || data.model_error || ('模型：' + (data.model_file || '未找到'));
  autoTagsStatusBox.innerHTML = '<div class="tag-hero"><div><b>' + percent + '%</b><span>本地谱面已分析</span></div><div class="tag-ring" style="--p:' + percent + '%"></div></div>' +
    '<div class="tag-stats"><div><span>有效谱面</span><b>' + total + '</b></div><div><span>已分析</span><b>' + analyzed + '</b></div><div><span>有模型标签</span><b>' + tagged + '</b></div></div>' +
    '<div class="auto-progress"><div class="auto-progress-head"><strong>' + taskName + '</strong><span class="badge">' + statusName + '</span></div><div class="auto-progress-bar"><span style="width:' + taskPercent + '%"></span></div><p class="muted">' + processed + ' / ' + taskTotal + ' · ' + escapeHtml(data.current || data.message || '等待操作') + '</p><p class="muted">' + escapeHtml(statusMessage) + '</p></div>';
}

async function loadAutoTagsStatus() {
  if (!autoTagsStatusBox || autoStatusRequestActive) return;
  autoStatusRequestActive = true;
  try {
    const data = await jsonFetch('/api/chart_tags/status');
    if (!data.ok) {
      autoModelBadge.textContent = '接口不可用';
      autoTagsStatusBox.innerHTML = '<div class="empty">' + escapeHtml(data.message || '本地模型状态加载失败') + '</div>';
      clearTimeout(autoStatusTimer);
      autoStatusTimer = setTimeout(loadAutoTagsStatus, autoStatusRetryDelay);
      autoStatusRetryDelay = Math.min(autoStatusRetryDelay * 2, 30000);
      return data;
    }
    renderAutoTagsStatus(data);
    autoStatusRetryDelay = 3000;
    clearTimeout(autoStatusTimer);
    autoStatusTimer = data.running ? setTimeout(loadAutoTagsStatus, 1600) : null;
    return data;
  } finally {
    autoStatusRequestActive = false;
  }
}

function openAutoModal(target) {
  target.classList.add('show');
}

function closeAutoModal(target) {
  target.classList.remove('show');
}

function updateRangeLabels(minId, maxId, minValueId, maxValueId) {
  const minInput = $(minId);
  const maxInput = $(maxId);
  if (Number(minInput.value) > Number(maxInput.value)) {
    if (document.activeElement === minInput) maxInput.value = minInput.value;
    else minInput.value = maxInput.value;
  }
  $(minValueId).textContent = Number(minInput.value).toFixed(1);
  $(maxValueId).textContent = Number(maxInput.value).toFixed(1);
}

function setDownloadModeVisibility() {
  const selected = document.querySelector('input[name="downloadMode"]:checked');
  $('downloadSearchLabel').classList.toggle('show', selected?.value === 'search');
}

async function startAutoDownload() {
  const mode = document.querySelector('input[name="downloadMode"]:checked')?.value || 'all';
  const query = $('downloadSearchQuery').value.trim();
  if (mode === 'search' && !query) {
    setAutoTagsResult(false, '搜索下载需要填写搜索词');
    return;
  }
  const data = await jsonFetch('/api/chart_tags/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ min_ds: Number($('downloadMinDs').value), max_ds: Number($('downloadMaxDs').value), mode, query })
  });
  setAutoTagsResult(Boolean(data.ok), data.message || JSON.stringify(data));
  if (data.ok) {
    closeAutoModal(autoDownloadModal);
    await loadAutoTagsStatus();
  }
}

async function startAutoAnalyze() {
  const mode = document.querySelector('input[name="analyzeMode"]:checked')?.value || 'new';
  const data = await jsonFetch('/api/chart_tags/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ min_ds: Number($('analyzeMinDs').value), max_ds: Number($('analyzeMaxDs').value), force: mode === 'force' })
  });
  setAutoTagsResult(Boolean(data.ok), data.message || JSON.stringify(data));
  if (data.ok) {
    closeAutoModal(autoAnalyzeModal);
    await loadAutoTagsStatus();
  }
}

async function stopAutoTags() {
  const data = await jsonFetch('/api/chart_tags/stop', { method: 'POST' });
  setAutoTagsResult(Boolean(data.ok), data.message || JSON.stringify(data));
  await loadAutoTagsStatus();
}

async function searchAutoTags() {
  if (!autoTagResultsBox) return;
  const range = dsRangeValue($('autoSearchDs').value);
  const query = $('autoSearchInput').value.trim();
  autoTagResultsBox.innerHTML = '<div class="empty">正在搜索谱面...</div>';
  const data = await jsonFetch('/api/chart_tags/search?q=' + encodeURIComponent(query) + '&min_ds=' + range[0] + '&max_ds=' + range[1] + '&limit=100');
  if (!data.ok) {
    autoTagResultsBox.innerHTML = '<div class="empty">' + escapeHtml(data.message || '搜索失败') + '</div>';
    return;
  }
  renderAutoTagSearchResults(data.items || []);
}

function renderAutoTagSearchResults(items) {
  if (!items.length) {
    autoTagResultsBox.innerHTML = '<div class="empty">没有找到匹配谱面</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  items.forEach(item => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chart-result';
    button.dataset.key = item.key;
    button.innerHTML = '<strong></strong><span></span><small></small>';
    button.querySelector('strong').textContent = item.title || item.key;
    button.querySelector('span').textContent = item.song_id + ' · ' + item.difficulty + ' · ' + item.level + ' · ' + item.ds + ' · ' + (item.artist || '');
    button.querySelector('small').textContent = '状态：' + (item.analysis_status === 'completed' ? '已分析' : '未分析') + ' · 标签：' + tagText(item.final_tags);
    frag.appendChild(button);
  });
  autoTagResultsBox.innerHTML = '';
  autoTagResultsBox.appendChild(frag);
}

function renderAutoTagDetail(item) {
  const tags = item.model_tags || [];
  const mapping = item.mapping || {};
  const fileMapping = item.file_mapping || {};
  const probabilities = Object.entries(item.model_probabilities || {}).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 10);
  const probabilityText = probabilities.map(([tag, score]) => escapeHtml(tag) + ' ' + (Number(score) * 100).toFixed(1) + '%').join(' · ') || '无';
  const windows = (item.model_windows || []).slice(0, 5).map(window => '[' + window.start + 's - ' + window.end + 's] ' + window.sequence).join('\n');
  autoTagDetailBox.className = '';
  autoTagDetailBox.innerHTML = '<div class="chart-editor-head"><div><h4></h4><p class="muted"></p></div><span class="badge"></span></div>' +
    '<div class="auto-tag-list">' + (tags.length ? tags.map(tag => '<span>' + escapeHtml(tag) + '</span>').join('') : '<span>无模型标签</span>') + '</div>' +
    '<div class="auto-detail-grid"><div><span class="muted">艺术家</span><b>' + escapeHtml(item.artist || '未知') + '</b></div><div><span class="muted">谱师</span><b>' + escapeHtml(item.charter || '未知') + '</b></div><div><span class="muted">定数 / BPM</span><b>' + escapeHtml(item.ds ?? '未知') + ' / ' + escapeHtml(item.bpm ?? '未知') + '</b></div><div><span class="muted">最终标签</span><b>' + escapeHtml(tagText(item.final_tags)) + '</b></div><div><span class="muted">模型概率 Top 10</span><b>' + probabilityText + '</b></div><div><span class="muted">运行时映射</span><b>' + escapeHtml((mapping.mapping_id || item.mapping_id || '') + ' · ' + (mapping.chart_section || item.chart_section || '')) + '</b></div><div><span class="muted">谱面文件</span><b>' + escapeHtml(mapping.chart_file || item.chart_file || item.source_file || item.source_path || '未知') + '</b></div><div><span class="muted">文件 SHA-256</span><b>' + escapeHtml(mapping.chart_file_sha256 || item.source_sha256 || '未知') + '</b></div><div><span class="muted">同文件难度</span><b>' + escapeHtml((fileMapping.chart_sections || []).map(section => (section.tag_key || section.mapping_id || '') + ' · ' + section.chart_section).join(' / ') || '未知') + '</b></div></div>' +
    '<details class="evidence-box"><summary>查看分析窗口</summary><pre class="auto-code"></pre></details>';
  autoTagDetailBox.querySelector('h4').textContent = item.title || item.key;
  autoTagDetailBox.querySelector('.chart-editor-head .muted').textContent = item.song_id + ' · ' + item.difficulty + ' · ' + item.level;
  autoTagDetailBox.querySelector('.badge').textContent = item.analysis_status === 'completed' ? '已完成' : (item.analysis_status || '未分析');
  autoTagDetailBox.querySelector('.auto-code').textContent = windows || '暂无分析窗口';
}

async function loadAutoTagDetail(key) {
  autoTagDetailBox.className = 'empty';
  autoTagDetailBox.textContent = '正在读取谱面标签...';
  const data = await jsonFetch('/api/chart_tags/' + encodeURIComponent(key));
  if (!data.ok) {
    autoTagDetailBox.textContent = data.message || '读取失败';
    return;
  }
  renderAutoTagDetail(data.item);
}

function renderLogs(entries) {
  if (!logsBox) return;
  if (!entries.length) {
    logsBox.innerHTML = '<div class="empty">暂无匹配日志</div>';
    return;
  }
  const fragment = document.createDocumentFragment();
  entries.forEach(entry => {
    const row = document.createElement('div');
    row.className = 'log-entry log-' + String(entry.level || 'INFO').toLowerCase();
    const head = document.createElement('div');
    head.className = 'log-entry-head';
    const level = document.createElement('b');
    level.textContent = entry.level || 'INFO';
    const time = document.createElement('time');
    time.textContent = entry.timestamp || '';
    head.append(level, time);
    const message = document.createElement('pre');
    message.textContent = entry.message || '';
    const source = document.createElement('small');
    source.textContent = (entry.logger || '') + (entry.source ? ' · ' + entry.source : '');
    row.append(head, message, source);
    fragment.appendChild(row);
  });
  logsBox.innerHTML = '';
  logsBox.appendChild(fragment);
}

async function loadLogs() {
  if (!logsBox || logsRequestActive) return;
  logsRequestActive = true;
  try {
    const query = logQuery?.value.trim() || '';
    const level = logLevel?.value || '';
    const data = await jsonFetch('/api/logs?limit=300&level=' + encodeURIComponent(level) + '&q=' + encodeURIComponent(query));
    if (!data.ok) {
      logsBox.innerHTML = '<div class="empty">' + escapeHtml(data.message || '日志读取失败') + '</div>';
      return;
    }
    renderLogs(data.entries || []);
  } finally {
    logsRequestActive = false;
  }
}

function updateLogTimer() {
  clearTimeout(logsTimer);
  logsTimer = null;
  if (!logAutoRefresh?.checked || !$('logs')?.classList.contains('active')) return;
  logsTimer = setTimeout(async () => {
    await loadLogs();
    updateLogTimer();
  }, 2500);
}

async function clearLogs() {
  const data = await jsonFetch('/api/logs/clear', { method: 'POST' });
  showToast(data.message || (data.ok ? '插件日志已清空' : '日志清空失败'));
  await loadLogs();
}

function tagText(tags) {
  return Array.isArray(tags) && tags.length ? tags.join(' / ') : '无';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

async function loadCommands() {
  const box = $('commandsBox');
  box.innerHTML = '';
  const data = await jsonFetch('/api/commands');
  if (!data.ok) {
    box.innerHTML = '<div class="empty">命令说明加载失败</div>';
    return;
  }
  data.commands.forEach(item => {
    const div = document.createElement('div');
    div.className = 'command';
    div.innerHTML = '<code></code><p class="muted"></p>';
    div.querySelector('code').textContent = item.command;
    div.querySelector('p').textContent = item.description;
    box.appendChild(div);
  });
}

function configInput(item) {
  const id = 'cfg_' + item.key;
  if (item.type === 'bool') {
    return '<label class="switch"><input id="' + id + '" data-key="' + item.key + '" data-type="bool" type="checkbox" ' + (item.value ? 'checked' : '') + '><span></span></label>';
  }
  const type = item.type === 'int' ? 'number' : (item.secret ? 'password' : 'text');
  const value = String(item.value ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  return '<input id="' + id + '" data-key="' + item.key + '" data-type="' + item.type + '" type="' + type + '" value="' + value + '">';
}

async function loadConfig() {
  const box = $('configForm');
  box.innerHTML = '<div class="empty">正在读取配置...</div>';
  const data = await jsonFetch('/api/config_summary');
  if (!data.ok) {
    box.innerHTML = '<div class="empty">配置加载失败</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  data.items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'config-item editable';
    div.innerHTML = '<div><div class="config-title"><code></code><span class="config-badge"></span></div><p class="muted"></p></div><div class="config-control">' + configInput(item) + '</div>';
    div.querySelector('code').textContent = item.label || item.key;
    div.querySelector('.config-badge').textContent = item.overridden ? 'WebUI 已覆盖' : 'AstrBot 配置';
    div.querySelector('p').textContent = item.hint || item.description || '';
    frag.appendChild(div);
  });
  const actions = document.createElement('div');
  actions.className = 'row config-actions';
  actions.innerHTML = '<button id="saveConfigBtn" class="secondary">保存 WebUI 配置</button><div id="configResult" class="result inline-result"></div>';
  frag.appendChild(actions);
  box.innerHTML = '';
  box.appendChild(frag);
  $('saveConfigBtn').addEventListener('click', () => withLoading($('saveConfigBtn'), saveConfig));
}

async function saveConfig() {
  const values = {};
  document.querySelectorAll('#configForm [data-key]').forEach(input => {
    const key = input.dataset.key;
    const type = input.dataset.type;
    values[key] = type === 'bool' ? input.checked : input.value;
  });
  const data = await jsonFetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values }) });
  const box = $('configResult');
  box.className = 'result inline-result ' + (data.ok ? 'ok' : 'err');
  box.textContent = data.message || JSON.stringify(data);
  showToast(box.textContent);
  await loadConfig();
}

async function doSave(sampleLines) {
  const payload = { name: personaNameInput.value.trim(), taste_roast: tasteRoastTextarea.value.trim(), special_note: specialNoteTextarea.value.trim(), samples: sampleLines };
  const data = await jsonFetch('/api/persona', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  setResult(Boolean(data.ok), data.message || JSON.stringify(data));
  await loadList();
  await loadOverview();
}

async function savePersona() {
  const name = personaNameInput.value.trim();
  const sampleLines = lines();
  if (!name) {
    setResult(false, '人格名称不能为空');
    return;
  }
  if (sampleLines.length === 0) {
    setResult(false, '聊天样本不能为空');
    return;
  }
  if (sampleLines.length < 50 && !pendingForce) {
    pendingForce = true;
    setResult(false, '聊天样本建议至少 50 条以获得更好的人格效果；当前 ' + sampleLines.length + ' 条。如果确认要保存，请再次点击保存按钮。');
    return;
  }
  pendingForce = false;
  await withLoading(saveBtn, () => doSave(sampleLines));
}

async function loadList() {
  listBox.innerHTML = '<div class="empty">正在加载人格库...</div>';
  const data = await jsonFetch('/api/personas');
  if (!data.ok) {
    listBox.innerHTML = '<div class="empty">' + (data.message || '人格库加载失败') + '</div>';
    return;
  }
  cachedPersonas = data.personas || [];
  renderPersonaLibrary(cachedPersonas);
  renderPersonaModal(cachedPersonas);
}

function personaMetaText(p) {
  return (p.has_taste_roast === 'true' ? '已设置品味锐评' : '未设置品味锐评') + ' · ' + (p.has_special_note === 'true' ? '已设置特殊说明' : '未设置特殊说明');
}

function renderPersonaLibrary(personas) {
  if (!personas.length) {
    listBox.innerHTML = '<div class="empty">还没有人格，先添加一个锐评灵魂吧 ♡</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  personas.forEach(p => {
    const item = document.createElement('div');
    item.className = 'persona-item';
    item.innerHTML = '<div class="persona-head"><strong></strong><span class="badge"></span></div><div class="muted"></div><div class="row"><button type="button" class="secondary small-btn" data-action="load" data-name="">加载</button><button type="button" class="danger small-btn" data-action="delete" data-name="">删除</button></div>';
    item.querySelector('strong').textContent = p.name;
    item.querySelector('.badge').textContent = p.sample_count + ' 条';
    item.querySelector('.muted').textContent = personaMetaText(p);
    item.querySelectorAll('button').forEach(btn => btn.dataset.name = p.name);
    frag.appendChild(item);
  });
  listBox.innerHTML = '';
  listBox.appendChild(frag);
}

function renderPersonaModal(personas) {
  if (!modalList) return;
  if (!personas.length) {
    modalList.innerHTML = '<div class="empty">当前没有可加载的人格</div>';
    return;
  }
  modalList.innerHTML = '';
  personas.forEach(p => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'modal-persona';
    item.dataset.name = p.name;
    item.innerHTML = '<strong></strong><span></span>';
    item.querySelector('strong').textContent = p.name;
    item.querySelector('span').textContent = p.sample_count + ' 条 · ' + personaMetaText(p);
    modalList.appendChild(item);
  });
}

async function loadPersona(name) {
  const data = await jsonFetch('/api/persona/' + encodeURIComponent(name) + '?limit=200&offset=0');
  if (!data.ok) {
    setResult(false, data.message || '加载失败');
    return;
  }
  personaNameInput.value = data.name || '';
  tasteRoastTextarea.value = data.taste_roast || '';
  specialNoteTextarea.value = data.special_note || '';
  samplesTextarea.value = (data.samples || []).join(String.fromCharCode(10));
  closePersonaModal();
  setResult(true, '已加载人格「' + (data.name || '') + '」最近 ' + (data.samples || []).length + ' 条样本 / 总计 ' + (data.sample_count || 0) + ' 条');
}

async function deletePersona(name) {
  if (!confirm('确认删除该锐评人格与样本？')) return;
  const data = await jsonFetch('/api/persona/' + encodeURIComponent(name), { method: 'DELETE' });
  setResult(Boolean(data.ok), data.message || JSON.stringify(data));
  await loadList();
  await loadOverview();
}

function clearForm() {
  personaNameInput.value = '';
  tasteRoastTextarea.value = '';
  specialNoteTextarea.value = '';
  samplesTextarea.value = '';
  pendingForce = false;
  setResult(true, '输入区已清空');
}

async function deleteCurrent() {
  if (!personaNameInput.value.trim()) {
    setResult(false, '请先填写或加载人格名称');
    return;
  }
  await deletePersona(personaNameInput.value.trim());
}

async function importJson() {
  const name = importPersonaNameInput.value.trim();
  const qq = importQQInput.value.trim();
  const file = jsonFileInput.files && jsonFileInput.files[0];
  if (!name) {
    setImportResult(false, '导入人格名称不能为空');
    return;
  }
  if (!qq) {
    setImportResult(false, '目标 QQ 不能为空');
    return;
  }
  if (!file) {
    setImportResult(false, '请选择 JSON 文件');
    return;
  }
  const form = new FormData();
  form.append('name', name);
  form.append('target_qq', qq);
  form.append('file', file);
  const res = await fetch(api('/api/import_json'), { method: 'POST', body: form });
  const data = await res.json().catch(() => ({ ok: false, message: '导入失败' }));
  setImportResult(Boolean(data.ok), data.message || JSON.stringify(data));
  if (data.ok) {
    personaNameInput.value = name;
    await loadList();
    await loadOverview();
  }
}

async function refreshAll() {
  await Promise.all([loadOverview(), loadCommands(), loadConfig(), loadList(), loadAutoTagsStatus(), searchAutoTags(), loadLogs()]);
}

function openPersonaModal() {
  renderPersonaModal(cachedPersonas);
  modal.classList.add('show');
}

function closePersonaModal() {
  modal.classList.remove('show');
}

window.closePersonaModal = closePersonaModal;

document.querySelectorAll('.nav').forEach(btn => btn.addEventListener('click', () => {
  document.querySelectorAll('.nav').forEach(n => n.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  $(btn.dataset.tab).classList.add('active');
  if (btn.dataset.tab === 'logs') {
    loadLogs();
    updateLogTimer();
  } else {
    clearTimeout(logsTimer);
    logsTimer = null;
  }
}));

saveBtn.addEventListener('click', savePersona);
clearBtn.addEventListener('click', clearForm);
deleteCurrentBtn.addEventListener('click', deleteCurrent);
loadPersonaBtn.addEventListener('click', openPersonaModal);
refreshBtn.addEventListener('click', () => withLoading(refreshBtn, async () => {
  await loadList();
  setResult(true, '人格库已刷新');
}));
importBtn.addEventListener('click', () => withLoading(importBtn, importJson));
$('refreshAllBtn').addEventListener('click', () => withLoading($('refreshAllBtn'), refreshAll));
$('openAutoDownloadBtn').addEventListener('click', () => openAutoModal(autoDownloadModal));
$('openAutoAnalyzeBtn').addEventListener('click', () => openAutoModal(autoAnalyzeModal));
$('closeAutoDownloadBtn').addEventListener('click', () => closeAutoModal(autoDownloadModal));
$('cancelAutoDownloadBtn').addEventListener('click', () => closeAutoModal(autoDownloadModal));
$('closeAutoAnalyzeBtn').addEventListener('click', () => closeAutoModal(autoAnalyzeModal));
$('cancelAutoAnalyzeBtn').addEventListener('click', () => closeAutoModal(autoAnalyzeModal));
$('confirmAutoDownloadBtn').addEventListener('click', () => withLoading($('confirmAutoDownloadBtn'), startAutoDownload));
$('confirmAutoAnalyzeBtn').addEventListener('click', () => withLoading($('confirmAutoAnalyzeBtn'), startAutoAnalyze));
$('autoStopBtn').addEventListener('click', () => withLoading($('autoStopBtn'), stopAutoTags));
$('refreshAutoTagsBtn').addEventListener('click', () => withLoading($('refreshAutoTagsBtn'), loadAutoTagsStatus));
$('autoSearchBtn').addEventListener('click', () => withLoading($('autoSearchBtn'), searchAutoTags));
$('autoSearchInput').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    withLoading($('autoSearchBtn'), searchAutoTags);
  }
});
$('autoSearchDs').addEventListener('change', searchAutoTags);
logLevel.addEventListener('change', loadLogs);
logQuery.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    loadLogs();
  }
});
logAutoRefresh.addEventListener('change', updateLogTimer);
$('refreshLogsBtn').addEventListener('click', () => withLoading($('refreshLogsBtn'), loadLogs));
$('clearLogsBtn').addEventListener('click', () => withLoading($('clearLogsBtn'), clearLogs));
document.querySelectorAll('input[name="downloadMode"]').forEach(input => input.addEventListener('change', setDownloadModeVisibility));
['downloadMinDs', 'downloadMaxDs'].forEach(id => $(id).addEventListener('input', () => updateRangeLabels('downloadMinDs', 'downloadMaxDs', 'downloadMinDsValue', 'downloadMaxDsValue')));
['analyzeMinDs', 'analyzeMaxDs'].forEach(id => $(id).addEventListener('input', () => updateRangeLabels('analyzeMinDs', 'analyzeMaxDs', 'analyzeMinDsValue', 'analyzeMaxDsValue')));
autoDownloadModal.addEventListener('click', event => { if (event.target === autoDownloadModal) closeAutoModal(autoDownloadModal); });
autoAnalyzeModal.addEventListener('click', event => { if (event.target === autoAnalyzeModal) closeAutoModal(autoAnalyzeModal); });
autoTagResultsBox.addEventListener('click', event => {
  const target = event.target.closest('.chart-result');
  if (target && target.dataset.key) loadAutoTagDetail(target.dataset.key);
});
jsonFileInput.addEventListener('change', () => {
  const file = jsonFileInput.files && jsonFileInput.files[0];
  fileText.textContent = file ? file.name : '选择文件';
});
listBox.addEventListener('click', event => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) return;
  const action = target.dataset.action;
  const name = target.dataset.name;
  if (!name) return;
  if (action === 'load') loadPersona(name);
  if (action === 'delete') deletePersona(name);
});
modalList.addEventListener('click', event => {
  const target = event.target.closest('.modal-persona');
  if (target && target.dataset.name) loadPersona(target.dataset.name);
});
modal.addEventListener('click', event => {
  if (event.target === modal) closePersonaModal();
});
window.addEventListener('load', async () => {
  await refreshAll();
  setTimeout(() => $('bootLoader').classList.add('hide'), 250);
});
