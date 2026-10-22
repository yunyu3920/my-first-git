/* 本地控制台前端：原生 JS，无构建。
 *
 * 几条来自后端的约定：
 * 1. 日志走 SSE，事件带 `id:`；浏览器重连时自动带 Last-Event-ID，这里不用管续传位置，
 *    但**刷新页面**后要从 /api/jobs/current 重新水合状态，否则会显示成"空闲"。
 * 2. 干跑/实跑由后端决定，前端只表达意图。
 * 3. 真实执行对"还没有商品ID 的新行"会被后端 409 拦下——前端要把那句话原样转达。
 */
'use strict';

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ 菜单 */
const MENU = [
  { key: 'run', label: '自动上架', ready: true },
  { key: 'autoMockup', label: '自动套图', ready: false },
  { key: 'psMockup', label: 'PS套图', ready: false },
  { key: 'collect', label: '收集文件', ready: true },
  { key: 'toolbox', label: '图片批处理生成清单', ready: true, note: '尺寸与格式 · 11 位 ID' },
  { key: 'settings', label: '设置', ready: true },
];

/* 未完成页面的说明：不放假按钮，写清缺什么 */
const TODO = {
  autoMockup: {
    title: '自动套图',
    sub: '把图案合成到模板底图上，批量出图。',
    note: '未完成。现有的图片管线只做「尺寸合规化」（找图 + 裁到 1340x1785），不含套图合成。',
    body: ['成像模块 imaging/ 目前只有规格校验与缩放裁剪，没有图层合成。',
           '要做需要先定：模板底图放哪、图案贴合位置怎么给（固定坐标还是按模板标注）。'],
  },
  psMockup: {
    title: 'PS套图',
    sub: '用你的样机 PSD，把印花贴进指定图层，输出成品图。',
    note: '未完成。需要你先给 3–5 个典型样机 PSD，并说明图层命名。',
    body: ['psd-tools 已装好（1.25.0），但还没有任何 PSD 处理代码。',
           'psd-tools 对智能对象变换、图层样式、剪贴蒙版支持有限，所以样机 PSD 的图层最好标准化命名：',
           'mockup-base（底图） / print-anchor（定位层，取其 bbox） / print-slot（贴图位置）。',
           '你给样例后我先做单个定位点的套图，跑通再扩到多个。'],
  },
};

let currentPage = 'run';

function renderMenu() {
  const nav = $('menu');
  nav.innerHTML = '';
  for (const item of MENU) {
    const btn = document.createElement('button');
    btn.innerHTML = `<span class="dot"></span><span>${item.label}</span>` +
      (item.ready ? '' : '<span class="todo">未完成</span>');
    btn.onclick = () => showPage(item.key);
    btn.dataset.key = item.key;
    nav.appendChild(btn);
  }
}

function showPage(key) {
  currentPage = key;
  for (const b of document.querySelectorAll('#menu button')) {
    b.classList.toggle('active', b.dataset.key === key);
  }
  const real = ['run', 'settings', 'collect', 'toolbox'].includes(key) ? key : 'todo';
  for (const p of document.querySelectorAll('.page')) {
    p.classList.toggle('active', p.dataset.page === real);
  }
  if (real === 'todo') renderTodo(key);
  if (key === 'settings') loadSettings();
}

function renderTodo(key) {
  const info = TODO[key];
  if (!info) return;
  $('todoTitle').textContent = info.title;
  $('todoSub').textContent = info.sub;
  $('todoNote').textContent = info.note;
  $('todoBody').innerHTML = info.body.map((l) => `<div class="card"><div class="kv">${esc(l)}</div></div>`).join('');
}

/* ------------------------------------------------------------------ 工具 */
function esc(text) {
  return String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: text }; }
  if (!res.ok) {
    data.error = data.error || data.detail || `HTTP ${res.status}`;
    // 404 几乎总是同一个原因：控制台窗口还开着**改代码之前**的那个旧进程。
    // 页面文件（HTML/JS）是每次从磁盘读的，所以看着是新的；后端路由却是启动时定死的。
    // 不解释这一句，用户只会看到干巴巴一个 "Not Found"。
    if (res.status === 404) {
      data.error =
        `${data.error}　—— 后端不认识这个接口。多半是控制台还开着改代码之前的旧进程` +
        `（页面的 HTML/JS 是现读的，所以看着是新的）。` +
        `把控制台窗口和它带的那个黑框都关掉，双击「0-打开网页控制台.bat」重开一次。`;
    }
  }
  return data;
}

function say(el, text, ok) {
  el.textContent = text;
  el.className = 'msg ' + (ok === true ? 'ok' : ok === false ? 'bad' : '');
}

/* ------------------------------------------------------------------ 日志 */
const LOG_ORDER = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };
let logEntries = [];
let lastSeq = 0;
let logSource = null;

function levelPass(entry) {
  const min = $('logLevel').value;
  if (!min) return true;
  return (LOG_ORDER[entry.level] || 0) >= (LOG_ORDER[min] || 0);
}

function renderLogs() {
  const box = $('logbox');
  const follow = $('logFollow').checked;
  box.innerHTML = logEntries.filter(levelPass).map((e) =>
    `<div class="logline ${esc(e.level)}"><span class="t">${esc(e.ts)}</span> - ${esc(e.msg)}</div>`
  ).join('');
  if (follow) box.scrollTop = box.scrollHeight;
}

function appendLog(entry) {
  logEntries.push(entry);
  if (entry.seq) lastSeq = Math.max(lastSeq, entry.seq);
  if (logEntries.length > 1500) logEntries = logEntries.slice(-1000);
  renderLogs();
}

function startLogStream() {
  if (logSource) logSource.close();
  logSource = new EventSource('/api/logs/stream?after=' + lastSeq);
  logSource.onmessage = (ev) => {
    try { appendLog(JSON.parse(ev.data)); } catch (e) { /* 忽略坏包 */ }
  };
  logSource.onerror = () => { /* EventSource 会自己重连，不关它 */ };
}

/* ------------------------------------------------------------------ 任务状态 */
let pollTimer = null;

function statusText(job) {
  const map = { idle: '空闲', running: '运行中', done: '已完成', failed: '失败', cancelled: '已取消' };
  let text = map[job.state] || job.state;
  if (job.busy) {
    text += `（第 ${job.current_row ?? '-'} 行）`;
    if (job.cancel_requested) text += ' · 已请求停止，等本行结束';
  } else if (job.error) {
    text += '：' + job.error;
  }
  return text;
}

async function refreshJob() {
  const job = await api('/api/jobs/current');
  const pill = $('statusPill');
  pill.textContent = statusText(job);
  pill.classList.toggle('running', !!job.busy);

  $('start').disabled = !!job.busy;
  $('stop').disabled = !job.busy;

  // 进度条
  const total = job.total || 0;
  const done = job.finished || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  $('runBar').style.width = job.busy || done ? pct + '%' : '0%';

  if (job.results && job.results.length) renderResults(job, done, total);
  renderBanner(job);

  if (!job.busy && job.state !== 'idle' && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    refreshRows();
    // 店铺/模板对不上 → 顺手把店小秘打开，好让你当场登好账号再跑
    maybeOpenAfterBlocking(job);
  }
  return job;
}

let openedForBlocking = false;

/** 核对失败时自动打开店小秘（只开一次，别刷一次开一次）。 */
function maybeOpenAfterBlocking(job) {
  if (openedForBlocking) return;
  const blocked = (job.results || []).some((r) =>
    (r.steps || []).some(
      (s) => s.status === 'fail' && (s.name === 'SHOP_CHECK' || s.name === 'FIND_TEMPLATE')
    )
  );
  if (!blocked) return;
  openedForBlocking = true;
  openSiteForTable().then((r) => {
    if (r && (r.message || r.error)) say($('shopMsg'), r.message || r.error, !!r.ok);
  });
}

function renderBanner(job) {
  const box = $('runBanner');
  if (!job.state || job.state === 'idle') { box.innerHTML = ''; return; }
  if (job.busy) {
    box.innerHTML = `<div class="banner ${job.live ? 'live' : 'dry'}">
      ${job.live ? '⚠ 真实执行中，会建商品、会写数据' : '🔸 干跑中，不会写任何数据'}
      &nbsp;·&nbsp;第 ${job.current_row ?? '-'} 行，已完成 ${job.finished}/${job.total}</div>`;
    return;
  }
  const results = job.results || [];
  const failed = results.filter((r) => !r.ok).length;
  const okCount = results.length - failed;
  const wbFailed = job.writeback_failed || [];
  const cls = failed || wbFailed.length ? 'live' : 'done';
  const word = { done: '已完成', failed: '失败', cancelled: '已停止' }[job.state] || job.state;

  // 店铺/模板这类"开跑就停"的问题，单独拉一条横幅喊出来——
  // 光看结果卡片里的 ❌ 容易漏掉，而这两种情况要人当场改输入（换登录身份 / 改表）。
  // **按错误内容去重**：一批 98 行同一个错，只写一条并给出行号范围。
  const blockingMap = new Map();
  for (const r of results) {
    for (const s of r.steps || []) {
      if (s.status === 'fail' && (s.name === 'SHOP_CHECK' || s.name === 'FIND_TEMPLATE')) {
        const key = `${s.name}::${s.detail}`;
        if (!blockingMap.has(key)) blockingMap.set(key, { name: s.name, detail: s.detail, rows: [] });
        blockingMap.get(key).rows.push(r.row_index);
      }
    }
  }
  const blocking = [...blockingMap.values()].map(
    (b) => `第 ${formatRowNumbers(b.rows)} 行 ${b.name}：${b.detail}`
  );

  box.innerHTML = `<div class="banner ${cls}">
    ${word}：共 ${job.total} 行，已跑 ${results.length}，成功 ${okCount}，失败 ${failed}
    ${job.live ? '（真实执行）' : '（干跑，未写任何数据）'}</div>` +
    (blocking.length
      ? `<div class="banner live">⛔ <b>已停止上架，需要你先处理：</b><br>` +
        blocking.map((b) => esc(b)).join('<br>') + `</div>`
      : '') +
    // 回写失败要单独喊一声：商品建好了（上架是成功的），但表格没记上，
    // 不补写的话下次跑这几行就是重复铺货
    (wbFailed.length
      ? `<div class="banner live">⚠ 第 ${wbFailed.join('、')} 行的商品ID/状态<b>没能写回表格</b>
         （多半是表开着 Excel/WPS）。商品已经建好了，别重跑这几行。关掉表后跑
         <code>temu-bot table writeback --live</code> 补上。</div>`
      : '');
}

/* 一组「错误完全相同」的行，只显示一遍，别把同一条话刷 98 遍。 */
function failureSignature(r) {
  if (r.error) return `ERR::${r.error}`;
  const bad = (r.steps || []).filter((s) => s.status === 'fail');
  return bad.map((s) => `${s.name}::${s.detail}`).join(' | ') || 'FAILED';
}

function stepsHtml(steps) {
  return (steps || []).map((s) => {
    const icon = { ok: '✅', dry: '🔸', skip: '⏭', fail: '❌' }[s.status] || '·';
    return `<div class="step"><span class="icon">${icon}</span>` +
           `<span class="name">${esc(s.name)}</span>` +
           `<span class="detail">${esc(s.detail)}</span></div>`;
  }).join('');
}

function renderResults(job, done, total) {
  const box = $('results');
  box.innerHTML = '';
  const results = job.results || [];

  // 按"成功 / 同一种失败"分组
  const groups = new Map();
  for (const r of results) {
    const key = r.ok ? 'ok' : failureSignature(r);
    let g = groups.get(key);
    if (!g) { g = { ok: r.ok, items: [], sample: r }; groups.set(key, g); }
    g.items.push(r);
  }
  // 失败在前；同组里行数多的在前
  const list = [...groups.values()].sort((a, b) => {
    if (a.ok !== b.ok) return a.ok ? 1 : -1;
    return b.items.length - a.items.length;
  });

  // 顶部汇总：一眼看清"哪种错误、各多少行"
  const failedCount = results.filter((r) => !r.ok).length;
  const summary = document.createElement('div');
  summary.className = 'card';
  summary.innerHTML = `<h3>汇总：共 ${total} 行，成功 ${results.length - failedCount}，失败 ${failedCount}</h3>` +
    list.map((g) => {
      const rows = g.items.map((r) => r.row_index);
      let label = '成功';
      if (!g.ok) {
        const bad = (g.sample.steps || []).filter((s) => s.status === 'fail').map((s) => s.name);
        label = bad.length ? bad.join(' + ') : '出错';
      }
      return `<div class="kv">${g.ok ? '✅' : '❌'} ${esc(label)}：` +
             `${g.items.length} 行（第 ${formatRowNumbers(rows)} 行）</div>`;
    }).join('');
  box.appendChild(summary);

  for (const g of list) {
    const rows = g.items.map((r) => r.row_index);
    const range = formatRowNumbers(rows);
    const card = document.createElement('div');
    card.className = 'card';

    if (g.ok) {
      // 成功行：一行只留商品ID，点开才看步骤。1 行时直接展开（省一次点击）。
      const detail = g.items.map(
        (r) => `<div class="kv">第 ${r.row_index} 行 · 商品ID <code>${esc(r.product_id || '(无)')}</code></div>`
      ).join('');
      if (g.items.length === 1) {
        card.innerHTML = `<h3>第 ${rows[0]} 行 <span class="badge ok">成功</span></h3>` +
          detail + `<div class="steps">${stepsHtml(g.sample.steps)}</div>`;
      } else {
        card.innerHTML = `<details><summary><b>✅ 成功 ${g.items.length} 行</b>` +
          `（第 ${range} 行）—— 点开看商品ID</summary>${detail}</details>`;
      }
      box.appendChild(card);
      continue;
    }

    const err = g.sample.error
      ? `<div class="kv" style="color:var(--bad)">${esc(g.sample.error)}</div>` : '';
    card.innerHTML =
      `<h3>第 ${range} 行 <span class="badge bad">有失败步骤</span>` +
      (g.items.length > 1 ? `<span class="count">共 ${g.items.length} 行，错误完全相同</span>` : '') +
      `</h3>${err}<div class="steps">${stepsHtml(g.sample.steps)}</div>`;
    box.appendChild(card);
  }

  if (done < total) {
    const wait = document.createElement('div');
    wait.className = 'card';
    wait.innerHTML = `<div class="kv">还有 ${total - done} 行未跑完…</div>`;
    box.appendChild(wait);
  }
}

/* ------------------------------------------------------------------ 系统文件对话框
 * 浏览器不允许网页拿本地真实路径，所以由**后端**在本机弹系统对话框。
 * 请求会一直挂着，直到用户在系统窗口里选完 —— 所以按钮要显示"等待选择中"。
 * 万一后端弹不出来（比如没有桌面会话），自动退回到下面那个站内目录树。
 */
async function askPath(kind, { title, initial = '', filters = '' }, button) {
  const label = button ? button.textContent : '';
  if (button) { button.disabled = true; button.textContent = '等待选择中…'; }
  try {
    const r = await api(`/api/dialog/${kind}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, initial, filters }),
    });
    if (r.error) return { error: r.error };
    return { path: r.path || '', cancelled: !!r.cancelled };
  } finally {
    if (button) { button.disabled = false; button.textContent = label; }
  }
}

/* ------------------------------------------------------------------ 站内目录树（兜底）
 * 用 pickerCtx 记住"这次是给谁选、选完怎么处理"。
 */
let pickerCtx = null;

function openPickerFor(boxId, hintId, startPath, { imagesOnly = false, allowPickFolder = false, onPick }) {
  pickerCtx = { boxId, hintId, imagesOnly, allowPickFolder, onPick };
  const box = $(boxId);
  box.classList.add('open');
  loadPickerDir(startPath || '');
}

async function loadPickerDir(path) {
  if (!pickerCtx) return;
  const { boxId, hintId, imagesOnly, allowPickFolder, onPick } = pickerCtx;
  const box = $(boxId);
  const data = await api(`/api/fs/list?path=${encodeURIComponent(path)}&images=${imagesOnly ? 'true' : 'false'}`);

  const rows = [`<div class="head">
      <button class="ghost" data-act="up">↑ 上一级</button>
      <input type="text" data-act="typed" value="${esc(data.path)}" placeholder="也可以直接粘贴路径">
      <button class="ghost" data-act="go">转到</button>
    </div>`];

  if (data.error) rows.push(`<div class="err">${esc(data.error)}</div>`);

  if (allowPickFolder && data.path) {
    rows.push(`<div class="item pick" data-pickdir="1">✔ 就用这个文件夹：${esc(data.path)}</div>`);
  }
  for (const d of data.dirs || []) {
    rows.push(`<div class="item dir" data-dir="${esc(d.path)}">📁 ${esc(d.name)}</div>`);
  }
  for (const f of data.files || []) {
    const size = f.size ? ` · ${(f.size / 1024).toFixed(0)}KB` : '';
    rows.push(`<div class="item" data-file="${esc(f.path)}">📄 ${esc(f.name)}${size}</div>`);
  }
  if (!data.dirs?.length && !data.files?.length && !data.error) {
    rows.push('<div class="err">这个目录是空的</div>');
  }
  box.innerHTML = rows.join('');

  const go = (p) => { loadPickerDir(p); };
  box.querySelector('[data-act=up]').onclick = () => { if (data.parent) go(data.parent); };
  box.querySelector('[data-act=go]').onclick = () => go(box.querySelector('[data-act=typed]').value);
  for (const el of box.querySelectorAll('[data-dir]')) el.onclick = () => go(el.dataset.dir);
  for (const el of box.querySelectorAll('[data-file]')) {
    el.onclick = async () => {
      await onPick(el.dataset.file);
      $(boxId).classList.remove('open');
      if (hintId) $(hintId).textContent = el.dataset.file;
    };
  }
  const pickDir = box.querySelector('[data-pickdir]');
  if (pickDir) {
    pickDir.onclick = async () => {
      await onPick(data.path);
      $(boxId).classList.remove('open');
      if (hintId) $(hintId).textContent = data.path;
    };
  }
  if (hintId) $(hintId).textContent = data.path || '未选择路径';
}

function closePickers() {
  for (const id of ['tablePicker', 'folderPicker', 'tbPicker']) {
    const el = $(id);
    if (el) el.classList.remove('open');
  }
}

async function saveTablePath(path) {
  const r = await api('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ changes: { project: { table: path } } }),
  });
  if (r.error) say($('tableHint'), r.error, false);
  else refreshRows();
}

/* ------------------------------------------------------------------ 行列表 */
let allRows = [];
// 表里"全空 / 缺文件夹或主图"的行号。它们读表时就被跳过，不会参与上架。
let droppedRows = [];
const picked = new Set();
// 用户动过勾选没有？没动过就按"默认全选"处理（第一次拿到行时全勾上）
let pickedTouched = false;

function selectedRows() {
  return allRows.filter((r) => picked.has(r.row));
}

async function refreshRows() {
  const skip = $('skipDone').checked;
  const data = await api(`/api/rows?limit=500&skip_done=${skip}`);
  const box = $('rowsBox');
  if (data.error) {
    droppedRows = [];
    box.innerHTML = `<div class="err" style="padding:10px">${esc(data.error)}</div>`;
    return;
  }
  allRows = data.rows || [];
  droppedRows = data.dropped || [];

  // 状态下拉的选项从数据里来
  const statuses = [...new Set(allRows.map((r) => r.status).filter(Boolean))];
  const sel = $('rowStatus');
  const keep = sel.value;
  sel.innerHTML = '<option value="">全部状态</option>' +
    statuses.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
  sel.value = statuses.includes(keep) ? keep : '';

  // 清掉已经不在列表里的选中项
  for (const n of [...picked]) {
    if (!allRows.some((r) => r.row === n)) picked.delete(n);
  }
  // **默认全选**（用户要求）：第一次拿到行就全勾上，之后尊重你自己的勾选
  if (!pickedTouched) {
    for (const r of allRows) picked.add(r.row);
    pickedTouched = true;
  }
  renderRows();
}

function filteredRows() {
  const q = $('rowSearch').value.trim().toLowerCase();
  const st = $('rowStatus').value;
  const onlyPicked = $('onlyPicked').checked;
  return allRows.filter((r) => {
    if (onlyPicked && !picked.has(r.row)) return false;
    if (st && r.status !== st) return false;
    if (!q) return true;
    return String(r.row).includes(q)
      || (r.title_cn || '').toLowerCase().includes(q)
      || (r.title_en || '').toLowerCase().includes(q)
      || (r.shop || '').toLowerCase().includes(q)
      || (r.main_image || '').toLowerCase().includes(q)
      || (r.sku_prefix_code || '').toLowerCase().includes(q);
  });
}

/* [3,4,6,7,8] → "3、4、6–8"：连续的行号收成区间，免得一长串数字 */
function formatRowNumbers(nums) {
  const sorted = [...new Set(nums)].sort((a, b) => a - b);
  const parts = [];
  let i = 0;
  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    parts.push(j > i ? `${sorted[i]}–${sorted[j]}` : `${sorted[i]}`);
    i = j + 1;
  }
  return parts.length > 12 ? parts.slice(0, 12).join('、') + ' 等' : parts.join('、');
}

/* 表里的空白行/半空行：读表时就跳过了，这里如实说一句，别让人以为少跑了行 */
function droppedNote() {
  if (!droppedRows.length) return '';
  return `<div class="hint" style="padding:8px 10px;border-bottom:1px solid var(--line)">
      表里另有 <b>${droppedRows.length}</b> 行是空白或不完整的（没有「文件夹路径」或「主图」）：
      第 ${formatRowNumbers(droppedRows)} 行。<b>已自动跳过，不影响其他行上架。</b>
    </div>`;
}

function renderRows() {
  const box = $('rowsBox');
  const rows = filteredRows();

  if (!rows.length) {
    box.innerHTML = `<div class="err" style="padding:10px">没有符合条件的行</div>` + droppedNote();
    updatePickCount();
    return;
  }

  const head = `<table class="rows"><thead><tr>
      <th style="width:36px"><input type="checkbox" id="checkAll"></th>
      <th style="width:46px">行</th>
      <th style="width:66px">状态</th>
      <th style="width:96px">商品</th>
      <th style="width:118px">SKU货号</th>
      <th>中文标题</th>
      <th style="width:110px">主图</th>
    </tr></thead><tbody>`;

  const body = rows.map((r) => {
    // 判断这一行真跑是否安全：表里已经有商品ID = 续跑，不会重复铺货
    const tag = r.product_id
      ? '<span class="badge safe">已有商品</span>'
      : '<span class="badge new">新建</span>';
    // 第 9 列：上架时填进每个 SKU 的货号。空的上不了架，列表里就标出来
    const sku = r.sku_prefix_code
      ? `<span class="t" title="${esc(r.sku_prefix_code)}">${esc(r.sku_prefix_code)}</span>`
      : '<span class="badge bad" title="第 9 列是空的，上架会被挡下">缺</span>';
    return `<tr data-row="${r.row}" class="${picked.has(r.row) ? 'picked' : ''}">
      <td><input type="checkbox" data-pick="${r.row}" ${picked.has(r.row) ? 'checked' : ''}></td>
      <td>${r.row}</td>
      <td>${esc(r.status || '')}</td>
      <td title="${esc(r.product_id || '')}">${tag}</td>
      <td>${sku}</td>
      <td class="t" title="${esc(r.title_cn)}">${esc(r.title_cn)}</td>
      <td class="t" title="${esc(r.main_image)}">${esc(r.main_image)}</td>
    </tr>`;
  }).join('');
  box.innerHTML = droppedNote() + head + body + '</tbody></table>';

  $('checkAll').onchange = (e) => {
    pickedTouched = true;                    // 你动过勾选了，之后不再自动全选
    if (e.target.checked) rows.forEach((r) => picked.add(r.row));
    else rows.forEach((r) => picked.delete(r.row));
    renderRows();
    renderPreview();
  };
  $('checkAll').checked = rows.length > 0 && rows.every((r) => picked.has(r.row));
  for (const cb of box.querySelectorAll('[data-pick]')) {
    cb.onchange = () => {
      pickedTouched = true;                  // 你动过勾选了，之后不再自动全选
      const n = Number(cb.dataset.pick);
      if (cb.checked) picked.add(n); else picked.delete(n);
      cb.closest('tr').classList.toggle('picked', cb.checked);
      updatePickCount();
      renderPreview();
    };
  }
  updatePickCount();
}

function updatePickCount() {
  const n = picked.size;
  const fresh = selectedRows().filter((r) => !r.product_id).length;
  $('pickCount').textContent = n
    ? `已选 ${n} 行${fresh ? `（其中 ${fresh} 行是新建）` : ''}`
    : '已选 0 行';
}

/* ------------------------------------------------------------------ 预览 */
let previewTimer = null;

function renderPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(loadPreview, 120);
}

async function loadPreview() {
  const box = $('previewBox');
  const chosen = selectedRows();
  if (!chosen.length) {
    box.innerHTML = '<div class="kv" style="color:var(--muted)">未选择行</div>';
    return;
  }
  const row = chosen[0];
  box.innerHTML = `<div class="preview"><div class="shot"><div class="none">加载中…</div></div>
    <div class="meta"><div class="t">第 ${row.row} 行</div>
    <div class="kv">正在读这一行的图…</div></div></div>`;

  const info = await api(`/api/row/${row.row}/images`);
  if (info.error) {
    box.innerHTML = `<div class="kv" style="color:var(--bad)">${esc(info.error)}</div>`;
    return;
  }

  const lines = [];
  lines.push(`<div class="kv">店铺：${esc(row.shop)}　状态：${esc(row.status || '—')}　${row.product_id ? '已有商品 ' + esc(row.product_id) : '<span class="badge new">新建商品</span>'}</div>`);
  // SKU 货号（第 9 列）：上架要用它填每个 SKU 的 extCode，不能用模板的；没有就跑不了
  const sku = row.sku_prefix_code || '';
  lines.push(
    `<div class="kv">SKU 货号（第 9 列）：` +
      (sku
        ? `${esc(sku)}　<span class="badge ok">会填进每个 SKU</span>`
        : `<span class="badge bad">空</span>　<span style="color:var(--bad)">没有它就只能沿用模板的货号，会被挡下</span>`) +
      `</div>`
  );
  lines.push(`<div class="kv">图片文件夹：${esc(info.folder || '(表里第 1 列是空的)')}${info.folder_exists ? `（共 ${info.folder_image_count} 张图）` : '　<span style="color:var(--bad)">目录不存在</span>'}</div>`);
  for (const it of info.items) {
    const okMark = it.found
      ? (it.problems.length ? '<span class="badge warn">要重出</span>' : '<span class="badge ok">合规</span>')
      : '<span class="badge bad">找不到</span>';
    const how = it.note ? `（${esc(it.note)}）` : '';
    const detail = it.found
      ? `${esc(it.size)} → ${esc(it.target)}`
      : esc(it.problems.join('；'));
    lines.push(`<div class="kv">${esc(it.spec_label)}：${esc(it.name)}${how} ${okMark} ${detail}</div>`);
  }
  if (chosen.length > 1) {
    lines.push(`<div class="kv">（还有 ${chosen.length - 1} 行已勾选，这里只预览第一行）</div>`);
  }

  box.innerHTML = `<div class="preview">
      <div class="shot" id="previewShot"><div class="none">无预览</div></div>
      <div class="meta"><div class="t">${esc((row.title_cn || '').slice(0, 40))}</div>${lines.join('')}</div>
    </div>`;

  if (row.main_image) {
    const img = new Image();
    img.alt = '主图预览';
    img.onload = () => { $('previewShot').innerHTML = ''; $('previewShot').appendChild(img); };
    img.onerror = () => { /* 保持"无预览"，上面的文字已经说明原因 */ };
    img.src = `/api/row/${row.row}/thumb?slot=main&w=360`;
  }
}

/* ------------------------------------------------------------------ 核对输入 */
async function checkInputs() {
  const box = $('preflightBox');
  box.innerHTML = '<div class="card"><div class="kv">正在核对…</div></div>';
  const data = await api('/api/preflight', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ images_dir: '', skip_done: $('skipDone').checked }),
  });
  if (data.error) { box.innerHTML = `<div class="card"><div class="kv" style="color:var(--bad)">${esc(data.error)}</div></div>`; return; }

  const okBadge = data.ok ? '<span class="badge ok">通过</span>' : '<span class="badge bad">不通过</span>';
  let html = `<div class="card"><h3>输入核对 ${okBadge}</h3>
      <div class="kv">信息表格：${esc(data.table_detail)}</div>
      <div class="kv">图片文件夹：${esc(data.folder_detail)}</div>
      <div class="kv">${esc(data.summary)}</div></div>`;

  if (data.blocked && data.blocked.length) {
    html += '<div class="card"><h3>会被挡下的行</h3>' + data.blocked.slice(0, 20).map((b) =>
      `<div class="kv">第 ${b.row_index} 行：${esc((b.problems || []).join('；'))}</div>`).join('') + '</div>';
  }
  box.innerHTML = html;
}

/* ------------------------------------------------------------ 打开店小秘 */
/** 表里这些行要用到哪些店铺名（去重）。 */
function tableShops() {
  return [...new Set(selectedRows().map((r) => (r.shop || '').trim()).filter(Boolean))];
}

/** 打开店小秘：**按表格里的店铺名**自动决定开哪个账号的浏览器。
 *  opts.wait = 没有登录态时，等用户登录完成再返回（控制台点一次「开始」就能走完）。 */
async function openSiteForTable(opts = {}) {
  const shops = tableShops();
  let q = `shop=${encodeURIComponent(shops[0] || '')}`;
  if (opts.wait) q += '&wait=1';
  return api(`/api/browser/open?${q}`, { method: 'POST' });
}

/* ------------------------------------------------------------ 店铺核对 */
/* 用户 2026-10-17：先核对店小秘，无误再开始上架；有误就提醒，不要执行上架。
 * 核对结果单独显示在 #shopCheckBox，不和「运行结果」混在一起。 */

function renderShopCheck(data) {
  const box = $('shopCheckBox');
  if (!data) { box.innerHTML = ''; return; }
  const msg = String(data.message || data.error || '')
    .split('\n').map((line) => esc(line)).join('<br>');
  const badge = data.ok
    ? '<span class="badge ok">通过</span>'
    : '<span class="badge bad">没通过</span>';
  const rows = (data.checks || []).map((c) => {
    const icon = c.ok ? '✅' : '❌';
    const where = c.rows && c.rows.length ? `第 ${c.rows.join('、')} 行` : '';
    const who = c.ok ? `登录身份「${c.login}」` : '没找到能上这家店的登录身份';
    return `<div class="kv">${icon} ${esc(c.shop || '(没填店铺名)')} · ${esc(where)} · ${esc(who)}</div>`;
  }).join('');
  box.innerHTML = `<div class="card" style="margin-top:8px">
      <h3>店铺核对 ${badge}</h3>
      <div class="kv" style="color:var(--${data.ok ? 'ok' : 'bad'})">${msg}</div>
      ${rows}
    </div>`;
}

async function checkShop(rows) {
  const list = rows || [...picked];
  if (!list.length) {
    say($('runMsg'), '先在 ② 里勾选要跑的行', false);
    return null;
  }
  $('shopCheckBox').innerHTML = '<div class="card"><div class="kv">正在核对店铺（实时读登录账号里的店铺列表）…</div></div>';
  const data = await api('/api/shops/precheck', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: list }),
  });
  renderShopCheck(data);
  return data;
}

/** 用户自己打开店小秘（不依赖「开始」）。 */
async function openSiteManually() {
  say($('shopMsg'), '正在打开店小秘…');
  const r = await openSiteForTable();
  say($('shopMsg'), r.message || r.error || '', !!r.ok);
}

/* ------------------------------------------------------------ 接口自检 */
/* 店小秘改版时，先看这里：哪个接口 FAIL，就是哪一块变了。 */

function renderApiCheck(data) {
  const box = $('dxmCheckBox');
  if (!data) { box.innerHTML = ''; return; }
  if (data.note) {
    box.innerHTML = `<div class="hint">${esc(data.note)}</div>`;
    return;
  }
  const reports = data.reports || [];
  if (!reports.length) {
    box.innerHTML = '<div class="hint">没有可检查的登录身份</div>';
    return;
  }
  box.innerHTML = reports.map((rep) => {
    const badge = rep.ok
      ? '<span class="badge ok">通过</span>'
      : '<span class="badge bad">没通过</span>';
    const rows = (rep.checks || []).map((c) => {
      const icon = c.skipped ? '⏭' : (c.ok ? '✅' : '❌');
      const ep = c.endpoint ? `　<code>${esc(c.endpoint)}</code>` : '';
      return `<div class="kv">${icon} ${esc(c.name)}：${esc(c.detail)}${ep}</div>`;
    }).join('');
    const err = rep.error
      ? `<div class="kv" style="color:var(--bad)">${esc(rep.error)}</div>` : '';
    return `<div class="card" style="margin-top:8px">
        <h3>接口自检 · ${esc(rep.login)} ${badge}</h3>${rows}${err}
      </div>`;
  }).join('');
}

async function diagnoseApi() {
  const rows = [...picked];
  $('dxmCheckBox').innerHTML = '<div class="card"><div class="kv">正在自检（只读，不会写任何数据）…</div></div>';
  const data = await api('/api/diagnose/api', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows, deep: true }),
  });
  renderApiCheck(data);
  if (data.error) say($('runMsg'), data.error, false);
}

/* ------------------------------------------------------------------ 运行 */
async function start() {
  const rows = [...picked];
  if (!rows.length) { say($('runMsg'), '先在 ② 里勾选要跑的行', false); return; }

  // ⓪ 全新用户还没有登录态：点一次「开始」就把登录窗口开起来，并等登录完成。
  //    不需要先去双击 1-登录店小秘.bat，也不用记登录身份叫什么。
  let openedForLogin = false;
  if ($('openBeforeLive').checked) {
    await refreshStatus();
    const hasSession = (window.__logins || []).some((l) => l && l.has_session);
    if (!hasSession) {
      say($('runMsg'), '还没有登录过店小秘——正在打开登录窗口，登录完成后会自动继续…');
      const o = await openSiteForTable({ wait: true });
      say($('shopMsg'), o.message || o.error || '', !o.error && o.logged_in !== false);
      if (o.error || o.logged_in === false) {
        say($('runMsg'), '登录没有完成，已取消上架（一行都没有跑）。登录店小秘后再点开始。', false);
        return;
      }
      openedForLogin = true;
    }
  }

  // ① 先核对店铺（只读）——对不上就停在这里，**一行都不跑**。
  //    这样 98 行不会各刷一条一模一样的 SHOP_CHECK 错误。
  say($('runMsg'), '先核对店铺…');
  let chk = await checkShop(rows);
  if (!chk || !chk.ok) {
    // 也可能是登录到了别的账号 / 店不在这个账号里：开窗口等用户登录、切店，
    // 成功后自动再核对一次，不用重新点开始。
    if ($('openBeforeLive').checked && !openedForLogin) {
      say($('runMsg'), '店铺还没对上——正在打开店小秘，登录/切店完成后会自动继续…');
      const o = await openSiteForTable({ wait: true });
      say($('shopMsg'), o.message || o.error || '', !o.error && o.logged_in !== false);
      openedForLogin = true;
      if (!o.error && o.logged_in !== false) {
        chk = await checkShop(rows);
      }
    }
    if (!chk || !chk.ok) {
      say($('runMsg'), '店铺核对没通过，已停止（没有执行上架）。先看上面「店铺核对」那一块。', false);
      return;
    }
  }

  // 运行方式固定为真实执行（页面上不再显示这个选项）
  const live = true;
  const chosen = selectedRows();
  const fresh = chosen.filter((r) => !r.product_id).length;

  if (live) {
    const swap = $('swapImages').checked;
    const op = $('op').value;
    const opLabel =
      {
        '1': '仅保存 —— 商品留在店小秘的待发布里，不发布',
        '2': '立即发布 —— 商品会**直接发布到 Temu**，发布后就去不掉"未发布"了',
        '3': '移入待发布 —— 只挪进待发布列表',
      }[op] || `op=${op}`;
    const ok = confirm(
      `真实执行确认\n\n` +
      `共 ${rows.length} 行，店铺「${tableShops().join('、') || '(表里没填)'}」。\n` +
      `登录身份由程序按表格里的店铺名自动确认。\n` +
      `会复制模板建出新商品、把主图传到图片空间。\n\n` +
      `保存后的动作：${opLabel}\n` +
      (fresh
        ? `\n其中 ${fresh} 行是「新建商品」：换图只换主图（新主图顶到轮播第 1 位，` +
          `模板的展示图保留不动），SKU 货号用表里第 9 列。\n` +
          `（${swap ? '强制换图已勾选' : '已有商品ID 的行不重复传图'}）\n` +
          `换图写不进去时流程会自己中止，不会存成"标题对、图片是模板的"错商品。\n`
        : '') +
      `\n确定要继续吗？`
    );
    if (!ok) return;
  }

  // 自动打开店小秘（可关）：你可以在窗口里自己再看一眼店铺/登录。
  // 打不开不拦——真正的把关是上面那次核对。刚才为了登录已经开过就别再开一次。
  if ($('openBeforeLive').checked && !openedForLogin) {
    say($('runMsg'), '正在打开店小秘…');
    const r = await openSiteForTable();
    say($('shopMsg'), r.message || r.error || '', !!r.ok);
  }

  say($('runMsg'), '正在启动…');
  const r = await api('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      shop: '',            // 不指定身份 —— 由流水线按表格里的店铺名自动认
      rows,
      images_dir: '',
      live,
      op: Number($('op').value),
      clean_images: $('cleanImages').checked,
      swap_images: $('swapImages').checked ? true : null,
    }),
  });
  if (r.error) {
    say($('runMsg'), r.error, false);
    if (r.shop_check) renderShopCheck(r.shop_check);
    if (r.api_check) renderApiCheck(r.api_check);
    if (r.blocked_rows) {
      // 把那几行在列表里标出来，方便改选
      for (const n of r.blocked_rows) picked.delete(n);
      renderRows();
      renderPreview();
    }
    return;
  }
  say($('runMsg'), `已开始（${r.mode}）`, true);
  $('results').innerHTML = '';
  openedForBlocking = false;            // 新一轮，允许再自动打开一次
  if (!pollTimer) pollTimer = setInterval(refreshJob, 1000);
  refreshJob();
}

async function stop() {
  const r = await api('/api/jobs/cancel', { method: 'POST' });
  say($('runMsg'), r.message || '已请求停止');
  refreshJob();
}

/* ------------------------------------------------------------------ 设置页 */
async function loadSettings() {
  const data = await api('/api/config');
  if (data.error) { $('settingsBody').innerHTML = `<div class="card">${esc(data.error)}</div>`; return; }

  const opt = (list, cur) => list.map((v) => `<option value="${v}" ${v === cur ? 'selected' : ''}>${v}</option>`).join('');

  const providers = (data.ai?.providers || []).map((p) => `
    <div class="kv">${esc(p.id)} · ${esc(p.model)} · 环境变量 <code>${esc(p.api_key_env)}</code>：
      <span class="badge ${p.key_set ? 'ok' : 'muted'}">${p.key_set ? '已设置' : '未设置'}</span>
    </div>`).join('');

  $('settingsBody').innerHTML = `
    <div class="card">
      <h3>运行</h3>
      <div class="field"><label>速度档位</label>
        <select id="setSpeed">${opt(['cautious', 'normal', 'fast'], data.runtime.speed)}</select></div>
      <div class="field"><label>日志级别</label>
        <select id="setLogLevel">${opt(['DEBUG', 'INFO', 'WARNING', 'ERROR'], data.runtime.log_level)}</select></div>
      <div class="check"><input type="checkbox" id="setScreenshot" ${data.runtime.screenshot_on_error ? 'checked' : ''}>
        <span>出错时自动截图</span></div>
    </div>

    <div class="card">
      <h3>浏览器</h3>
      <div class="kv">provider：${esc(data.browser.provider)}　channel：${esc(data.browser.channel || '(Playwright 自带)')}</div>
      <div class="check"><input type="checkbox" id="setHeadless" ${data.browser.headless ? 'checked' : ''}>
        <span>无头模式（不显示窗口）</span></div>
      <div class="field"><label>视口宽度</label><input type="number" id="setVw" value="${data.browser.viewport.width}"></div>
      <div class="field"><label>视口高度</label><input type="number" id="setVh" value="${data.browser.viewport.height}"></div>
    </div>

    <div class="card">
      <h3>路径</h3>
      <div class="field"><label>商品信息表</label><input type="text" id="setTable" value="${esc(data.project.table)}"></div>
      <div class="kv">图片批处理是内置功能，不需要旧版桌面工具箱目录。</div>
    </div>

    <div class="card">
      <h3>AI 密钥</h3>
      ${providers || '<div class="kv">未配置 provider</div>'}
    </div>

    <div class="card">
      <h3>配置文件位置</h3>
      <div class="kv">主配置（只读）：${esc(data.paths.main)}</div>
      <div class="kv">本机覆盖层（保存写这里）：${esc(data.paths.local)}</div>
    </div>`;
}

async function saveSettings() {
  const changes = {
    runtime: {
      speed: $('setSpeed').value,
      log_level: $('setLogLevel').value,
      screenshot_on_error: $('setScreenshot').checked,
    },
    browser: {
      headless: $('setHeadless').checked,
      viewport: { width: Number($('setVw').value), height: Number($('setVh').value) },
    },
    project: { table: $('setTable').value.trim() },
  };
  const r = await api('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ changes }),
  });
  if (r.error) { say($('settingsMsg'), '保存失败：' + r.error, false); return; }
  say($('settingsMsg'), '已保存到 ' + r.path, true);
  loadSettings();
  refreshStatus();
  refreshRows();
}

/* ------------------------------------------------------------------ 启动 */
let statusPrefilled = false;

function renderTopbar(s) {
  const box = $('topbar');
  if (!s) { box.innerHTML = ''; return; }
  const name = (s.table || '').split(/[\\/]/).pop() || '(未设)';
  const err = s.table_error || '';
  const tableMissing = err.includes('找不到商品表') || err.includes('找不到表格');
  const rows = err
    ? `<span class="warn" title="${esc(err)}">${tableMissing ? '未设置商品表' : '表格读取失败'}</span>`
    : `${s.table_rows} 行`;
  box.innerHTML = `
    <span class="k">当前商品信息表</span>
    <span class="v" title="${esc(s.table || '')}">${esc(name)}</span>
    <span class="sep">|</span><span>${rows}</span>
    <span class="sep">|</span><span class="k">速度</span><span>${esc(s.speed || '')}</span>
    <span class="sep">|</span><span class="k">版本</span><span>${esc(s.version || '')}</span>`;
}

async function refreshStatus() {
  const s = await api('/api/status');
  if (s.error) return null;
  window.__shops = s.shops || [];
  window.__logins = s.logins || [];
  renderTopbar(s);
  $('tablePath').value = s.table || '';

  // 收集页的店铺下拉：店铺名给个建议就行，**不再拿配置当权威**
  const dl = $('shopOptions');
  if (dl) dl.innerHTML = (s.shops || []).map((x) => `<option value="${esc(x.name)}">`).join('');

  // 首次进来自动填一次（之后不覆盖你改过的值）。
  // **填上次用过的那一组**（店铺名 + 模板 SPU）——你往往连着收集同一家店的素材。
  // 从没记过就**留空**，不拿配置里那家"配了模板SPU"的店来凑数：
  // 那家店很可能是你早就不用的（配置是死的），填上去只会让人以为程序认错了店。
  // 想省事可以从店铺名的下拉建议里挑一个，挑完 SPU 会自动带出来（见 autofillSpu）。
  if (!statusPrefilled) {
    statusPrefilled = true;
    const last = loadCollectLast();
    if (!$('collectShop').value) $('collectShop').value = last.shop || '';
    if (!$('collectSpu').value) $('collectSpu').value = last.spu || '';
    checkCollectShop();
  }
  return s;
}

// 收集页那两个框记住上次用的值。存浏览器本地（localStorage）：
// 不用改配置文件，也不会因为换个店铺就污染 config.local.yaml。
const COLLECT_LAST_KEY = 'temu_bot.collect.last';

function loadCollectLast() {
  try {
    return JSON.parse(localStorage.getItem(COLLECT_LAST_KEY) || '{}') || {};
  } catch (e) {
    return {};
  }
}

function saveCollectLast() {
  try {
    localStorage.setItem(COLLECT_LAST_KEY, JSON.stringify({
      shop: $('collectShop').value.trim(),
      spu: $('collectSpu').value.trim(),
    }));
  } catch (e) {
    /* 隐私模式之类存不了：那就记不住，不影响功能 */
  }
}

/** 收集页：只提示"上架时会按登录账号里实际的店来核对"，**不拿配置当权威**。 */
function checkCollectShop() {
  const el = $('collectShopWarn');
  if (!el) return;
  const name = $('collectShop').value.trim();
  if (!name) { el.textContent = ''; return; }
  const known = (window.__shops || []).map((x) => x.name);
  if (known.includes(name)) { el.textContent = ''; return; }
  el.innerHTML = `<span class="tag">提示</span> 配置里没登记这家店——没关系，` +
    `上架时会拿它去对<b>你登录的账号里实际的店</b>，对得上就照跑，对不上会停下提醒。`;
}

/** 输入的店铺名若在配置里，自动带出它的模板 SPU（只在 SPU 为空或还停在别家的值时）。 */
function autofillSpu() {
  const name = $('collectShop').value.trim();
  const shop = (window.__shops || []).find((x) => x.name === name);
  if (!shop) return;
  const cur = $('collectSpu').value.trim();
  const isOtherShopSpu = (window.__shops || []).some((x) => x.template_spuid && x.template_spuid === cur);
  if (!cur || isOtherShopSpu) $('collectSpu').value = shop.template_spuid || '';
}

/* ------------------------------------------------------------------ 收集文件 */
let collectScan = null;

function collectPayload(live) {
  saveCollectLast();   // 真用上了，顺手记住这一组店铺名 + 模板 SPU
  return {
    folder: $('collectFolder').value.trim(),
    shop: $('collectShop').value.trim(),
    template_spu: $('collectSpu').value.trim(),
    main_index: Number($('collectMainIndex').value) || 1,
    out: $('collectOut').value.trim(),
    img_name_as_skc: $('collectSkc').checked,
    force_material: $('collectForceMat').checked,
    material_width: Number($('collectMatW').value) || 800,
    material_height: Number($('collectMatH').value) || 800,
    recursive: $('collectType').value === 'deep',
    title_list: $('collectTitleList').value.trim(),
    live,
  };
}

function renderCollectScan(data, elapsedMs) {
  collectScan = data;
  const box = $('collectBox');
  const elapsed = elapsedMs ? `　用时 ${elapsedMs} ms` : '';

  const rows = (data.rows || []).map((r) => {
    const bad = (r.problems || []).length
      ? `<span class="tag bad" title="${esc(r.problems.join('；'))}">规格</span> ${esc(r.problems.join('；'))}`
      : (r.note ? esc(r.note) : '');
    const title = (r.title_cn || r.title_en)
      ? `<span title="${esc([r.title_cn, r.title_en].filter(Boolean).join('\n'))}">${esc((r.title_cn || r.title_en).slice(0, 24))}</span>` +
        (r.title_cn && !r.title_en ? ' <span class="tag">无英文</span>' : '')
      : '<span style="color:var(--muted)">—</span>';
    return `<tr>
      <td>${r.index}</td>
      <td class="t" title="${esc(r.main)}">${esc(r.main)}</td>
      <td class="t" title="${esc(r.material)}">${esc(r.material) || '<span style="color:var(--bad)">—</span>'}</td>
      <td class="t">${esc(r.stem)}</td>
      <td class="t">${title}</td>
      <td class="t">${bad}</td>
    </tr>`;
  }).join('');

  // 标题清单的匹配情况（没选清单就没有这一段）
  const tl = data.title_list || {};
  let titleLine = '';
  if (tl.path) {
    if (tl.error) {
      titleLine = `<div class="kv" style="color:var(--bad)">标题清单读不了：${esc(tl.error)}</div>`;
    } else {
      titleLine = `<div class="kv">标题清单：匹配 ${tl.matched}/${data.row_count} 行｜清单里 ${tl.total} 个 ID（${tl.with_en} 个带英文）` +
        (tl.shifted
          ? `　<span style="color:var(--warn)">英文列比 ID 列多一行，已按 ID 自动对齐</span>`
          : '') +
        (tl.missing_count
          ? `　<span style="color:var(--warn)">${tl.missing_count} 个 ID 没找到：${esc((tl.missing || []).slice(0, 10).join('、'))}</span>`
          : '') +
        `</div>`;
    }
  }

  let html = `<div class="card"><h3>扫描结果</h3>
      <div class="kv">${esc(data.summary)}<span style="color:var(--muted)">${elapsed}</span></div>
      <div class="kv">将写出：${esc(data.out_path)}${data.out_renamed ? '　<span class="badge warn">该文件已存在 → 会自动改名另存</span>' : ''}</div>
      ${titleLine}
      ${data.spec_problem_count ? `<div class="kv" style="color:var(--warn)">⚠ 有 ${data.spec_problem_count} 行的主图尺寸不符合 Temu 要求（3:4、宽≥1340、高≥1785、≤2MB）——收集不影响，但上架时会被平台打回，建议先修图。</div>` : ''}
    </div>`;

  if (rows) {
    html += `<div class="card"><h3>会收成这些行（前 ${Math.min(data.rows.length, 200)} 行）</h3>
      <div class="scrollbox"><table class="rows"><thead><tr>
        <th style="width:44px">#</th><th>主图</th><th>素材图</th><th>前缀编码</th><th>标题</th><th>备注 / 主图规格</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
      ${data.rows_truncated ? `<div class="kv">…还有 ${data.rows_truncated} 行未显示</div>` : ''}
    </div>`;
  }

  if (data.skipped?.length) {
    html += `<div class="card"><h3>跳过的（${data.skipped_count}）</h3>` +
      data.skipped.map((s) =>
        `<div class="kv">${esc(s.name || '(整个文件夹)')}：${esc(s.reason)}</div>`).join('') +
      `${data.skipped_count > data.skipped.length ? `<div class="kv">…还有 ${data.skipped_count - data.skipped.length} 条</div>` : ''}</div>`;
  }

  box.innerHTML = html;

  $('collectCount').textContent = `收 ${data.row_count} 行 / 跳过 ${data.skipped_count} 张`;
  $('collectGo').disabled = !data.row_count || !data.folder;

  const outBanner = $('collectOutBanner');
  if (data.out_renamed) {
    outBanner.style.display = '';
    outBanner.innerHTML = `⚠ 目标文件已存在，<b>不会覆盖它</b>，会另存为 <code>${esc(data.out_path)}</code>`;
  } else {
    outBanner.style.display = 'none';
  }
}

/** 跑一个会花时间的操作：按钮禁用 + 显示用时，让"到底有没有在跑"一眼可见。 */
async function withBusy(button, busyText, fn) {
  const label = button.textContent;
  const t0 = performance.now();
  button.disabled = true;
  button.textContent = busyText;
  try {
    return { result: await fn(), ms: Math.round(performance.now() - t0) };
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function doCollectScan() {
  const folder = $('collectFolder').value.trim();
  if (!folder) { say($('collectMsg'), '先选素材文件夹', false); return; }
  say($('collectMsg'), '');
  $('collectGo').disabled = true;

  const { result: data, ms } = await withBusy($('collectScan'), '扫描中…', () =>
    api('/api/collect/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectPayload(false)),
    })
  );

  if (data.error) { say($('collectMsg'), data.error, false); $('collectBox').innerHTML = ''; return; }
  renderCollectScan(data, ms);
  if (data.unknown_shop) say($('collectMsg'), data.unknown_shop, false);
}

async function doCollectRun() {
  if (!collectScan) { await doCollectScan(); if (!collectScan) return; }
  const ok = confirm(
    `生成表格\n\n` +
    `收成 ${collectScan.row_count} 行，写出到：\n${collectScan.out_path}\n\n` +
    (collectScan.out_renamed ? `（该文件名已存在，会改名另存，绝不覆盖原文件）\n\n` : '') +
    `确定要生成吗？`
  );
  if (!ok) return;

  $('collectBar').style.width = '30%';
  const { result: data, ms } = await withBusy($('collectGo'), '生成中…', () =>
    api('/api/collect/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...collectPayload(true), live: true }),
    })
  );
  $('collectBar').style.width = '100%';

  if (data.error) { say($('collectMsg'), data.error, false); return; }
  say($('collectMsg'), `${data.summary}　（用时 ${ms} ms）`, true);

  const box = $('collectBox');
  box.insertAdjacentHTML('afterbegin', `<div class="banner done">
    已生成：${esc(data.path)}（${data.rows} 行）
    ${data.generated_materials?.length ? `，另生成了 ${data.generated_materials.length} 张素材图` : ''}
    　用时 ${ms} ms</div>`);

  // 生成完可以直接把它设为当前上架用的表
  box.insertAdjacentHTML('beforeend', `<div class="card">
      <div class="kv">要拿它去上架？点下面的按钮设为当前商品信息表。</div>
      <div style="margin-top:8px"><button class="ghost" id="useCollectTable">用这张表上架</button></div>
    </div>`);
  $('useCollectTable').onclick = async () => {
    await saveTablePath(data.path);
    await refreshStatus();
    showPage('run');
  };
}

async function pickTableNative() {
  const btn = $('pickTable');
  const r = await askPath('file', {
    title: '选择商品信息表',
    initial: $('tablePath').value || '',
    filters: 'Excel 表格|*.xlsx;*.xlsm;*.xls;;所有文件|*.*',
  }, btn);
  if (r.error) { fallbackToTree('table'); say($('tableHint'), `系统对话框打不开（${r.error}），改用页面内选择。`, false); return; }
  if (r.cancelled || !r.path) return;
  $('tablePath').value = r.path;
  await saveTablePath(r.path);
}

async function pickFolderNative() {
  const btn = $('pickFolder');
  const r = await askPath('folder', {
    title: '选择素材文件夹',
    initial: $('collectFolder').value || '',
  }, btn);
  if (r.error) { fallbackToTree('folder'); say($('collectMsg'), `系统对话框打不开（${r.error}），改用页面内选择。`, false); return; }
  if (r.cancelled || !r.path) return;
  $('collectFolder').value = r.path;
  $('collectFolderHint').textContent = r.path;
  collectScan = null;
  $('collectGo').disabled = true;
}

/** 选「图片文件名清单」(.xlsx / .csv)——按 11 位 ID 填中英文标题用。 */
async function pickTitleListNative() {
  const btn = $('pickTitleList');
  const r = await askPath('file', {
    title: '选择图片文件名清单',
    initial: $('collectTitleList').value || $('collectFolder').value || '',
    filters: '图片文件名清单|*.xlsx;*.xlsm;*.csv;;所有文件|*.*',
  }, btn);
  if (r.error) { say($('collectMsg'), `系统对话框打不开（${r.error}），可以直接在框里粘贴路径。`, false); return; }
  if (r.cancelled || !r.path) return;
  $('collectTitleList').value = r.path;
  collectScan = null;
  $('collectGo').disabled = true;
}

function fallbackToTree(which) {
  if (which === 'table') {
    openPickerFor('tablePicker', 'tableHint', $('tablePath').value, {
      onPick: (p) => { $('tablePath').value = p; return saveTablePath(p); },
    });
  } else {
    openPickerFor('folderPicker', 'collectFolderHint', $('collectFolder').value, {
      allowPickFolder: true,
      onPick: async (p) => {
        $('collectFolder').value = p;
        collectScan = null;
        $('collectGo').disabled = true;
      },
    });
  }
}

async function pickOutputNative() {
  const btn = $('pickOut');
  const cur = $('collectOut').value.trim() || $('collectFolder').value.trim();
  const r = await askPath('save', {
    title: '选择结果表保存位置',
    initial: cur ? `${cur}\\商品信息表.xlsx` : '商品信息表.xlsx',
    filters: 'Excel 表格|*.xlsx',
  }, btn);
  if (r.error) { say($('collectMsg'), `系统对话框打不开：${r.error}`, false); return; }
  if (r.cancelled || !r.path) return;
  $('collectOut').value = r.path;
}

/* ------------------------------------------------------------------ 工具箱 */
// 页面上就一件事：尺寸与格式 → 11 位 ID（一条龙）。
// 骨架是"预览（只读）→ 执行（后台任务 + 进度条）"。逻辑全在后端。
const TB_TITLE = {
  combo: '尺寸与格式 + 11 位 ID',
};

function tbFolder() {
  return $('tbFolder').value.trim();
}

function tbComboPayload() {
  const wantCsv = $('tbExportCsv').checked;
  const wantXlsx = $('tbExportXlsx').checked;
  // 两个都不勾 = ""，后端会明确报「请至少勾选一种输出格式」
  const fmt = wantCsv && wantXlsx ? 'both' : wantCsv ? 'csv' : wantXlsx ? 'xlsx' : '';
  return {
    folder: tbFolder(),
    width: Number($('tbWidth').value) || 1340,
    height: Number($('tbHeight').value) || 1785,
    target_format: $('tbFormat').value,
    fit: $('tbFit').value,
    // 页面上不显示质量，固定用默认值（命令行仍可 --quality 调）
    quality: 95,
    id_length: Number($('tbRenameLen').value) || 11,
    csv_path: $('tbExportPath').value.trim(),
    export_format: fmt,
  };
}

const TB_STATUS_CLASS = { ok: 'ok', failed: 'bad', skipped: 'muted', would: 'warn' };

function renderToolReport(tool, data, elapsedMs) {
  const box = $(`tb${tool[0].toUpperCase()}${tool.slice(1)}Box`);
  const elapsed = elapsedMs ? `　用时 ${elapsedMs} ms` : '';
  const items = data.items || [];

  const rows = items.map((it) => {
    const cls = TB_STATUS_CLASS[it.status] || 'muted';
    const target = it.target ? it.target.split(/[\\/]/).pop() || it.target : '';
    return `<tr>
      <td class="t" title="${esc(it.target)}">${esc(it.name)}</td>
      <td class="t">${esc(it.action)}</td>
      <td class="t" title="${esc(it.target)}">${esc(target)}</td>
      <td><span class="badge ${cls}">${esc(it.status_label)}</span></td>
      <td class="t">${esc(it.detail)}</td>
    </tr>`;
  }).join('');

  let html = `<div class="card"><h3>${esc(TB_TITLE[tool] || tool)}<span style="color:var(--muted)">${elapsed}</span></h3>
      <div class="kv">${esc(data.summary || '')}</div>
      <div class="kv">文件夹：${esc(data.folder || '')}</div></div>`;

  if (rows) {
    html += `<div class="card"><div class="scrollbox"><table class="rows"><thead><tr>
        <th>文件</th><th>动作</th><th>目标</th><th>结果</th><th>说明</th>
      </tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }
  if (data.outputs?.length) {
    html += `<div class="card"><h3>产出</h3>` +
      data.outputs.map((p) => `<div class="kv"><code>${esc(p)}</code></div>`).join('') +
      `</div>`;
  }
  box.innerHTML = html;

  const counts = data.counts || {};
  const label = Object.entries(counts)
    .filter(([, n]) => n)
    .map(([k, n]) => `${n} ${({ ok: '成功', failed: '失败', skipped: '跳过', would: '待执行' })[k] || k}`)
    .join(' / ');
  $(`tb${tool[0].toUpperCase()}${tool.slice(1)}Count`).textContent = label;
  return data.ok;
}

let comboPoll = null;

async function doComboScan() {
  const msg = $('tbComboMsg');
  const scanBtn = $('tbComboScan');
  const runBtn = $('tbComboRun');
  if (!tbFolder()) { say(msg, '先在 ① 里选文件夹', false); return; }
  const p = tbComboPayload();
  if (!p.export_format) { say(msg, '至少勾选一种清单格式（CSV 或 XLSX）', false); return; }

  say(msg, '正在预览（图片多时需要一会儿）…');
  $('tbComboBar').style.width = '0%';
  runBtn.disabled = true;
  const { result: data, ms } = await withBusy(scanBtn, '预览中…', () =>
    api('/api/toolbox/combo/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    })
  );
  if (data.error) { say(msg, data.error, false); return; }
  renderToolReport('combo', data, ms);
  runBtn.disabled = false;
  const failed = (data.counts || {}).failed || 0;
  say(msg, failed ? `有 ${failed} 项会失败` : '预览完成（还没有动文件）', failed ? false : undefined);
}

async function doComboRun() {
  const msg = $('tbComboMsg');
  const runBtn = $('tbComboRun');
  if (!tbFolder()) { say(msg, '先在 ① 里选文件夹', false); return; }
  const p = tbComboPayload();
  if (!p.export_format) { say(msg, '至少勾选一种清单格式（CSV 或 XLSX）', false); return; }

  const fitLabel = $('tbFit').selectedOptions[0]?.textContent || p.fit;
  const fmt = (p.target_format || 'jpg').toUpperCase();
  const ok = confirm(
    `一条龙执行\n\n` +
    `文件夹：${p.folder}\n` +
    `① 尺寸与格式：${p.width} × ${p.height} · ${fmt}（${fitLabel}）\n` +
    `   ⚠ 会直接覆盖原图，文件名不变；png 等原文件会被删掉。\n` +
    `② 11 位 ID：加 ID → 导出清单 → 还原成纯 11 位 ID。\n\n` +
    `确定开始吗？（建议先点「预览」）`
  );
  if (!ok) return;

  say(msg, '正在启动…');
  $('tbComboBar').style.width = '0%';
  const start = await withBusy(runBtn, '启动中…', () =>
    api('/api/toolbox/combo/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    })
  );
  if (start.error) { say(msg, start.error, false); return; }
  runBtn.disabled = true;
  say(msg, '开始处理…');
  if (comboPoll) clearInterval(comboPoll);
  comboPoll = setInterval(pollCombo, 500);
  pollCombo();
}

async function pollCombo() {
  const s = await api('/api/toolbox/combo/status');
  if (s.error) return;
  $('tbComboBar').style.width = `${s.percent || 0}%`;
  if (s.state === 'running') {
    say($('tbComboMsg'), s.message || '处理中…');
    return;
  }
  if (comboPoll) { clearInterval(comboPoll); comboPoll = null; }
  const runBtn = $('tbComboRun');
  if (runBtn) runBtn.disabled = false;
  if (s.state === 'failed') {
    say($('tbComboMsg'), s.error || '处理失败', false);
    return;
  }
  if (s.state === 'done' && s.result) {
    const ok = renderToolReport('combo', s.result, 0);
    say($('tbComboMsg'), s.result.summary || '已完成', ok);
  }
}

/** 「常用规格」下拉 → 回填宽高/格式；选手动改了尺寸就自动切到「自定义」。 */
function tbApplySizePreset() {
  const value = $('tbSizePreset').value;
  if (value === 'custom') return;
  const [size, fmt] = value.split(':');
  const [w, h] = size.split('x');
  $('tbWidth').value = w;
  $('tbHeight').value = h;
  $('tbFormat').value = fmt;
}

function tbMarkSizeCustom() {
  $('tbSizePreset').value = 'custom';
}

async function doToolboxProbe() {
  const box = $('tbProbeBox');
  const { result: data } = await withBusy($('tbProbe'), '检查中…', () => api('/api/toolbox/probe'));
  if (data.error) { box.innerHTML = `<div class="card">${esc(data.error)}</div>`; return; }

  const led = data.ledger || {};
  const pill = data.pillow || {};
  const files = (led.files || []).map((f) =>
    `<div class="kv"><code>${esc(f.path)}</code> —— ${f.error ? `<span style="color:var(--bad)">${esc(f.error)}</span>` : `${f.count} 个号${f.is_project ? '（新号写这里）' : '（旧台账，只读来去重）'}`}</div>`
  ).join('');
  $('tbProbeBox').innerHTML =
    `<div class="card"><h3>自检</h3>
      <div class="kv">合并去重后已用 ID：<b>${led.merged_total ?? '?'}</b> 个</div>
      ${files}
      <div class="kv">Pillow ${esc(pill.version || '')}：avif
        <span class="badge ${pill.avif ? 'ok' : 'bad'}">${pill.avif ? '支持' : '不支持'}</span>　webp
        <span class="badge ${pill.webp ? 'ok' : 'bad'}">${pill.webp ? '支持' : '不支持'}</span>　heic
        <span class="badge ${pill.heic ? 'ok' : 'muted'}">${pill.heic ? '支持' : '不支持'}</span></div>
      <div class="kv">可转格式：${(data.targets || []).join(' / ')}</div>
      <div class="kv">重命名模式：${(data.modes || []).join(' / ')}</div>
    </div>`;
}

async function pickToolboxFolder() {
  const btn = $('tbPickFolder');
  const r = await askPath('folder', { title: '选择文件夹', initial: tbFolder() }, btn);
  if (r.error) {
    openPickerFor('tbPicker', null, tbFolder(), {
      allowPickFolder: true,
      onPick: async (p) => { $('tbFolder').value = p; resetToolboxButtons(); },
    });
    return;
  }
  if (r.cancelled || !r.path) return;
  $('tbFolder').value = r.path;
  resetToolboxButtons();
}

/** 换了文件夹 → 之前的预览作废，执行按钮重新锁上。 */
function resetToolboxButtons() {
  const runBtn = $('tbComboRun');
  if (runBtn) runBtn.disabled = true;
  const count = $('tbComboCount');
  if (count) count.textContent = '';
  const bar = $('tbComboBar');
  if (bar) bar.style.width = '0%';
}

function wire() {
  $('pickTable').onclick = pickTableNative;
  $('pickFolder').onclick = pickFolderNative;
  $('pickTitleList').onclick = pickTitleListNative;
  $('pickOut').onclick = pickOutputNative;
  $('collectScan').onclick = doCollectScan;
  $('collectGo').onclick = doCollectRun;
  $('collectShop').onchange = () => { autofillSpu(); checkCollectShop(); saveCollectLast(); };
  $('collectShop').oninput = checkCollectShop;
  $('collectSpu').onchange = saveCollectLast;
  $('collectSpu').oninput = saveCollectLast;

  // 工具箱：尺寸与格式 + 11 位 ID（一条龙） + 文件夹选择 + 自检
  $('tbPickFolder').onclick = pickToolboxFolder;
  $('tbComboScan').onclick = doComboScan;
  $('tbComboRun').onclick = doComboRun;
  $('tbSizePreset').onchange = tbApplySizePreset;
  $('tbWidth').oninput = tbMarkSizeCustom;
  $('tbHeight').oninput = tbMarkSizeCustom;
  $('tbFormat').onchange = tbMarkSizeCustom;
  $('tbProbe').onclick = doToolboxProbe;

  $('pickAll').onclick = () => {
    pickedTouched = true;
    filteredRows().forEach((r) => picked.add(r.row));
    renderRows();
    renderPreview();
  };
  $('pickInvert').onclick = () => {
    pickedTouched = true;
    for (const r of filteredRows()) {
      if (picked.has(r.row)) picked.delete(r.row); else picked.add(r.row);
    }
    renderRows();
    renderPreview();
  };
  $('pickClear').onclick = () => {
    pickedTouched = true;
    picked.clear();
    renderRows();
    renderPreview();
  };
  $('onlyPicked').onchange = renderRows;
  $('reloadRows').onclick = refreshRows;
  $('skipDone').onchange = refreshRows;
  $('rowSearch').oninput = renderRows;
  $('rowStatus').onchange = renderRows;
  $('checkInputs').onclick = checkInputs;
  $('checkShopBtn').onclick = () => checkShop();
  $('dxmCheckBtn').onclick = diagnoseApi;
  $('openSiteBtn').onclick = openSiteManually;
  $('start').onclick = start;
  $('stop').onclick = stop;
  $('saveSettings').onclick = saveSettings;
  $('logLevel').onchange = renderLogs;
  $('logFollow').onchange = renderLogs;
  $('logClear').onclick = () => { logEntries = []; renderLogs(); };

  // 回车 = 开始（在搜索框里回车除外）
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || currentPage !== 'run') return;
    if (e.target && e.target.tagName === 'INPUT' && e.target.id !== 'rowSearch') return;
    if (!$('start').disabled) start();
  });
}

async function boot() {
  renderMenu();
  wire();
  showPage('run');
  startLogStream();
  await refreshStatus();
  await refreshRows();
  const job = await refreshJob();
  if (job && job.busy && !pollTimer) pollTimer = setInterval(refreshJob, 1000);
}

boot();
