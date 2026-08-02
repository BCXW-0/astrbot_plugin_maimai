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
const tagsBox = $('tagsBox');
const tagSearchInput = $('tagSearchInput');
const tagSearchBtn = $('tagSearchBtn');
const tagResultsBox = $('tagResultsBox');
const tagEditorBox = $('tagEditorBox');
const tagManageResultBox = $('tagManageResult');
const toast = $('toast');
const modal = $('personaModal');
const modalList = $('personaModalList');
let pendingForce = false;
let cachedPersonas = [];
let allowedChartTags = [];
let currentChartTagKey = '';

function api(path) {
  const token = location.search.replace(/^\?/, '');
  if (!token) return path;
  return path + (path.includes('?') ? '&' : '?') + token;
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
  const res = await fetch(api(path), options);
  return await res.json().catch(() => ({ ok: false, message: '请求失败' }));
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

async function loadTagsStatus() {
  if (!tagsBox) return;
  tagsBox.innerHTML = '<div class="empty">正在读取谱面标签任务状态...</div>';
  const data = await jsonFetch('/api/chart_tags/status');
  if (!data.ok) {
    tagsBox.innerHTML = '<div class="empty">谱面标签状态加载失败</div>';
    return;
  }
  renderTagsStatus(data);
}

function renderTagsStatus(data) {
  const total = Number(data.total || 0);
  const tagged = Number(data.tagged || 0);
  const untagged = Number(data.untagged ?? Math.max(0, total - tagged));
  const percent = total ? Math.round(tagged * 1000 / total) / 10 : 0;
  tagsBox.innerHTML = '<div class="tag-hero"><div><b>' + percent + '%</b><span>已完成标签抽取</span></div><div class="tag-ring" style="--p:' + percent + '%"></div></div>' +
    '<div class="tag-stats"><div><span>总谱面</span><b>' + total + '</b></div><div><span>有标签的谱面数</span><b>' + tagged + '</b></div><div><span>无标签的谱面数</span><b>' + untagged + '</b></div></div>' +
    '<div class="tip">联网补缺使用玩家资料和保守关键词规则；本地谱面元数据审计在插件外离线完成。</div>' +
    '<div class="tag-meta"><p><b>状态：</b>' + (data.running ? '运行中' : '未运行') + '</p><p><b>当前：</b>' + (data.current_title || data.last_title || '暂无') + '</p><p><b>批次：</b>' + (data.message || '暂无') + '</p><p><b>文件：</b>' + (data.path || '') + '</p><p><b>最近错误：</b>' + (data.last_error || '无') + '</p></div>' +
    '<div class="row"><button id="generateTagsBtn" class="secondary">生成基础标签文件</button><button id="startTagsBtn">自动更新补缺</button><button id="stopTagsBtn" class="danger">停止任务</button><button id="refreshTagsBtn" class="ghost">刷新状态</button></div><div id="tagsResult" class="result"></div>';
  $('generateTagsBtn').addEventListener('click', () => withLoading($('generateTagsBtn'), generateTagsBase));
  $('startTagsBtn').addEventListener('click', () => withLoading($('startTagsBtn'), startTagsJob));
  $('stopTagsBtn').addEventListener('click', () => withLoading($('stopTagsBtn'), stopTagsJob));
  $('refreshTagsBtn').addEventListener('click', () => withLoading($('refreshTagsBtn'), loadTagsStatus));
}

function setTagsResult(ok, message) {
  const box = $('tagsResult');
  if (!box) return;
  box.className = 'result ' + (ok ? 'ok' : 'err');
  box.textContent = message;
  showToast(message);
}

async function generateTagsBase() {
  const data = await jsonFetch('/api/chart_tags/generate', { method: 'POST' });
  setTagsResult(Boolean(data.ok), data.message || JSON.stringify(data));
  await loadTagsStatus();
}

async function startTagsJob() {
  const data = await jsonFetch('/api/chart_tags/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ batch_size: 50 }) });
  setTagsResult(Boolean(data.ok), data.message || JSON.stringify(data));
  await loadTagsStatus();
}

async function stopTagsJob() {
  const data = await jsonFetch('/api/chart_tags/stop', { method: 'POST' });
  setTagsResult(Boolean(data.ok), data.message || JSON.stringify(data));
  await loadTagsStatus();
}

function setTagManageResult(ok, message) {
  if (!tagManageResultBox) return;
  tagManageResultBox.className = 'result ' + (ok ? 'ok' : 'err');
  tagManageResultBox.textContent = message;
  showToast(message);
}

function tagText(tags) {
  return Array.isArray(tags) && tags.length ? tags.join(' / ') : '无';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

async function searchChartTags() {
  if (!tagResultsBox) return;
  const query = tagSearchInput.value.trim();
  tagResultsBox.innerHTML = '<div class="empty">正在搜索谱面...</div>';
  const data = await jsonFetch('/api/chart_tags/search?q=' + encodeURIComponent(query) + '&limit=60');
  if (!data.ok) {
    tagResultsBox.innerHTML = '<div class="empty">' + (data.message || '搜索失败') + '</div>';
    return;
  }
  allowedChartTags = data.allowed_tags || allowedChartTags;
  renderTagSearchResults(data.items || []);
}

function renderTagSearchResults(items) {
  if (!items.length) {
    tagResultsBox.innerHTML = '<div class="empty">没有找到匹配谱面</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  items.forEach(item => {
    const div = document.createElement('button');
    div.type = 'button';
    div.className = 'chart-result';
    div.dataset.key = item.key;
    div.innerHTML = '<strong></strong><span></span><small></small>';
    div.querySelector('strong').textContent = item.title || item.key;
    div.querySelector('span').textContent = item.song_id + ' · ' + item.difficulty + ' · ' + item.level + ' · ' + (item.type || '');
    div.querySelector('small').textContent = '标签：' + tagText(item.final_tags) + ' / 手动：' + tagText(item.manual_tags);
    frag.appendChild(div);
  });
  tagResultsBox.innerHTML = '';
  tagResultsBox.appendChild(frag);
}

async function loadChartTagDetail(key) {
  currentChartTagKey = key;
  tagEditorBox.innerHTML = '<div class="empty">正在读取谱面标签...</div>';
  const data = await jsonFetch('/api/chart_tags/' + encodeURIComponent(key));
  if (!data.ok) {
    tagEditorBox.innerHTML = '<div class="empty">' + (data.message || '读取失败') + '</div>';
    return;
  }
  allowedChartTags = data.allowed_tags || allowedChartTags;
  renderChartTagEditor(data.item);
}

function renderChartTagEditor(item) {
  const selected = new Set(item.manual_tags || []);
  const checks = allowedChartTags.map(tag => '<label class="tag-check"><input type="checkbox" value="' + escapeHtml(tag) + '" ' + (selected.has(tag) ? 'checked' : '') + '><span>' + escapeHtml(tag) + '</span></label>').join('');
  const evidence = (item.evidence || []).slice(0, 6).map(e => '<li><a href="' + escapeHtml(e.url || '#') + '" target="_blank" rel="noreferrer"></a><p></p></li>').join('');
  tagEditorBox.innerHTML = '<div class="chart-editor-head"><div><h4></h4><p class="muted"></p></div><span class="badge"></span></div>' +
    '<div class="tag-meta compact"><p><b>谱师：</b>' + escapeHtml(item.charter || '未知') + '</p><p><b>定数：</b>' + escapeHtml(item.ds ?? '未知') + ' / 拟合 ' + escapeHtml(item.fit_diff ?? '未知') + '</p><p><b>物量：</b>' + escapeHtml(JSON.stringify(item.notes || {})) + '</p><p><b>自动标签：</b>' + escapeHtml(tagText(item.llm_tags)) + '</p><p><b>最终标签：</b>' + escapeHtml(tagText(item.final_tags)) + '</p></div>' +
    '<div class="tag-check-grid">' + checks + '</div>' +
    '<div class="row"><button id="saveManualTagsBtn">保存手动标签</button><button id="clearManualTagsBtn" class="ghost">清空手动标签</button></div>' +
    '<details class="evidence-box"><summary>查看搜索证据</summary><ol>' + evidence + '</ol></details>';
  tagEditorBox.querySelector('h4').textContent = item.title || item.key;
  tagEditorBox.querySelector('.chart-editor-head .muted').textContent = item.song_id + ' · ' + item.difficulty + ' · ' + item.level + ' · ' + (item.type || '');
  tagEditorBox.querySelector('.badge').textContent = item.tag_status || '未处理';
  tagEditorBox.querySelectorAll('.evidence-box li').forEach((li, idx) => {
    const e = (item.evidence || [])[idx] || {};
    li.querySelector('a').textContent = e.title || e.url || '搜索证据';
    li.querySelector('p').textContent = e.summary || '';
  });
  $('saveManualTagsBtn').addEventListener('click', () => withLoading($('saveManualTagsBtn'), saveManualTags));
  $('clearManualTagsBtn').addEventListener('click', () => withLoading($('clearManualTagsBtn'), clearManualTags));
}

function selectedManualTags() {
  return Array.from(tagEditorBox.querySelectorAll('.tag-check input:checked')).map(input => input.value);
}

async function saveManualTags() {
  if (!currentChartTagKey) {
    setTagManageResult(false, '请先选择谱面');
    return;
  }
  const data = await jsonFetch('/api/chart_tags/' + encodeURIComponent(currentChartTagKey), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ manual_tags: selectedManualTags() })
  });
  setTagManageResult(Boolean(data.ok), data.message || JSON.stringify(data));
  if (data.ok) {
    renderChartTagEditor(data.item);
    await loadTagsStatus();
    await searchChartTags();
  }
}

async function clearManualTags() {
  tagEditorBox.querySelectorAll('.tag-check input').forEach(input => input.checked = false);
  await saveManualTags();
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
  await Promise.all([loadOverview(), loadCommands(), loadConfig(), loadList(), loadTagsStatus(), searchChartTags()]);
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
tagSearchBtn.addEventListener('click', () => withLoading(tagSearchBtn, searchChartTags));
tagSearchInput.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    withLoading(tagSearchBtn, searchChartTags);
  }
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
tagResultsBox.addEventListener('click', event => {
  const target = event.target.closest('.chart-result');
  if (target && target.dataset.key) loadChartTagDetail(target.dataset.key);
});
window.addEventListener('load', async () => {
  await refreshAll();
  setTimeout(() => $('bootLoader').classList.add('hide'), 250);
});
