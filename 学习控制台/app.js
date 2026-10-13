let csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = { data: null, filter: 'all', course: 'all', busy: false, editOriginal: null, documentFilter: 'all', documentSearch: '', searchResults: null, settingsTab: 'account', logs: null, logFilter: 'all' };
let searchTimer, searchRequest;
const dateFormats = new Map();
function dateFormatter(locale, options) { const key = JSON.stringify([locale, options]); if (!dateFormats.has(key)) (() => { if (dateFormats.size >= 24) dateFormats.delete(dateFormats.keys().next().value); dateFormats.set(key, new Intl.DateTimeFormat(locale, options)); })(); return dateFormats.get(key); }
let settingsTrigger, logRequest, documentsRequest, documentsRevision;
const historicalNotice = '部分页面保留其他学年的日期；已采用当前学年且相互印证的安排';
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[char]);

function hkDate(date, options = { month: 'long', day: 'numeric', weekday: 'short' }) {
  if (!date) return '日期待确认';
  const parsed = new Date(`${date.slice(0, 10)}T00:00:00+08:00`);
  return dateFormatter('zh-HK', { timeZone: 'Asia/Hong_Kong', ...options }).format(parsed);
}

function hkMoment(value) {
  if (!value) return '尚未同步';
  return dateFormatter('zh-HK', {
    timeZone: 'Asia/Hong_Kong', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(new Date(value));
}

function hkLongMoment(value) {
  if (!value) return '暂无记录';
  return dateFormatter('zh-HK', {
    timeZone: 'Asia/Hong_Kong', year: 'numeric', month: 'long', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false
  }).format(new Date(value));
}

function syncAge(value) {
  if (!value) return '点击上方按钮同步最新资料';
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 2) return '刚刚同步';
  if (minutes < 60) return `${minutes} 分钟前同步`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前同步`;
  return `${Math.floor(minutes / 1440)} 天前同步`;
}

function freshnessText(data) {
  const source = data.freshness || {};
  const age = data.lastSync ? `${syncAge(data.lastSync)} · ${data.courseCount} 门课程` : syncAge(null);
  if (source.state === 'partial') return `本轮同步未完整完成 · 最近完整采集：${hkLongMoment(source.lastCompleteAt)}`;
  if (source.state === 'stale') return `${age} · 已超过 24 小时，请更新核对`;
  return age;
}

function relative(days) {
  if (days === 0) return '今天';
  if (days === 1) return '明天';
  return `${days} 天后`;
}

function gradeNumber(value) {
  return gradeNumber.formatter.format(value);
}

gradeNumber.formatter = new Intl.NumberFormat('zh-HK', { maximumFractionDigits: 2 });

function kindLabel(kind) {
  return { midterm: '期中考试', final: '期末考试', test: '其他测试', assignment: '作业 / 事项' }[kind] || '安排';
}

function validLink(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && parsed.hostname === 'canvas.polyu.edu.hk' ? parsed.href : '';
  } catch {
    return '';
  }
}

function anchor(url, text, className = '') {
  const href = validLink(url);
  if (!href) return `<span class="${className}">${escapeHtml(text)}</span>`;
  return `<a class="${className}" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`;
}

function showNotice(message, type = 'info') {
  const notice = $('#notice');
  notice.hidden = !message;
  notice.className = `notice ${type}`;
  notice.textContent = message;
  const settingsNotice = $('#settingsNotice');
  settingsNotice.hidden = !message || !$('#settingsDialog').open;
  settingsNotice.className = `notice ${type}`;
  settingsNotice.textContent = message;
}

function setBusy(busy) {
  state.busy = busy;
  $('#refreshButton').disabled = busy || Boolean(state.data?.connection?.native && !state.data.connection.authorized);
  $('#addEventButton').disabled = busy || !state.data?.courseCount;
  $('#eventForm button[type="submit"]').disabled = busy;
  $('#refreshButton').classList.toggle('spinning', busy);
  $('#refreshButton').innerHTML = busy
    ? '<span class="refresh-mark" aria-hidden="true">↻</span> 正在同步 Canvas…'
    : '<span class="refresh-mark" aria-hidden="true">↻</span> 更新 Canvas';
}

function updateActiveNav() {
  const target = $('#settingsDialog').open && state.settingsTab === 'courses' ? '#courses' : location.hash || '#top';
  document.querySelectorAll('.side-nav a').forEach((link) => {
    link.classList.toggle('active', link.getAttribute('href') === target);
  });
}

function selectSettingsTab(tab, focus = false) {
  const button = document.querySelector(`[data-settings-tab="${tab}"]`);
  if (!button) return;
  state.settingsTab = tab;
  document.querySelectorAll('[data-settings-tab]').forEach((item) => {
    const active = item.dataset.settingsTab === tab;
    item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll('[data-settings-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.settingsPanel !== tab || (tab === 'account' && !state.data?.connection?.native);
  });
  if (state.data && tab === 'documents') {
    const revision = dataEtag || localRevision;
    if (documentsRevision === revision && state.data.documents?.records) renderDocuments(state.data);
    else {
      documentsRequest?.abort();
      const request = documentsRequest = new AbortController(), current = state.data;
      fetch('/api/documents', { cache: 'no-store', signal: request.signal }).then(r => r.ok ? r.json() : {}).then(documents => {
        if (request === documentsRequest && current === state.data && pageVisible() && $('#settingsDialog').open && state.settingsTab === 'documents') {
          state.data.documents = documents; documentsRevision = revision; renderDocuments(state.data);
        }
      }).catch(() => {});
    }
  }
  if (state.data && tab === 'courses') renderCourses(state.data);
  if (tab === 'logs') refreshLogs();
  if (focus) button.focus();
  $('.settings-body').scrollTop = 0;
  updateActiveNav();
}

function openSettings(tab = state.settingsTab, trigger = document.activeElement) {
  const dialog = $('#settingsDialog');
  if (!dialog.open) {
    settingsTrigger = trigger;
    dialog.showModal();
    document.body.classList.add('settings-open');
    $('#settingsButton').setAttribute('aria-expanded', 'true');
    $('#settingsNotice').hidden = true;
  }
  selectSettingsTab(tab);
  $('#closeSettingsButton').focus();
}

function settingsClosed() {
  document.body.classList.remove('settings-open');
  $('#settingsButton').setAttribute('aria-expanded', 'false');
  document.querySelectorAll('[data-settings-panel]').forEach((panel) => { panel.hidden = true; });
  logRequest?.abort(); searchRequest?.abort(); clearTimeout(searchTimer);
  updateActiveNav();
  if (settingsTrigger?.isConnected) settingsTrigger.focus();
}

function renderLogs() {
  const logs = state.logs;
  if (!logs) return;
  const rows = logs.events.filter((row) => state.logFilter === 'all' || ['failure', 'partial'].includes(row.result));
  $('#logList').innerHTML = rows.length ? rows.map((row) => `<article class="log-row log-${escapeHtml(row.result)}"><time>${escapeHtml(hkLongMoment(row.at))}</time><div><strong>${escapeHtml(row.label)}</strong><span>${escapeHtml(row.resultLabel)}${row.codeLabel ? ` · ${escapeHtml(row.codeLabel)}` : ''}</span></div><small>v${escapeHtml(row.version)} · ${row.origin === 'native' ? '桌面' : '服务'}</small></article>`).join('') : '<p class="empty-state">暂无符合筛选的日志。</p>';
  $('#logStatus').textContent = `${logs.events.length} / ${logs.limit} 条 · ${logs.persistent ? '当前账号' : '本次运行'}${logs.storageIssue ? ' · 日志保存未完成，当前记录仍可查看' : ''}`;
}

async function refreshLogs() {
  if (!$('#settingsDialog').open || state.settingsTab !== 'logs') return;
  logRequest?.abort();
  const request = logRequest = new AbortController();
  try {
    const response = await fetch('/api/diagnostics', { headers: { 'X-PolyU-Console': csrf }, signal: request.signal, cache: 'no-store' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '日志读取未完成');
    if (request !== logRequest) return;
    state.logs = result; renderLogs();
  } catch (error) {
    if (error.name !== 'AbortError' && request === logRequest) $('#logStatus').textContent = error.message;
  }
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PolyU-Console': csrf },
    body: JSON.stringify(payload || {})
  });
  const result = await response.json();
  if (!response.ok) {
    if (result.data) render(result.data);
    throw new Error(result.error || '操作失败，请稍后重试。');
  }
  if (result.data) render(result.data);
  return result;
}

function render(data) {
  const contentKey = JSON.stringify([data.today, data.version, data.courses, data.events, data.undated, data.recent, data.grades, data.lastSync, data.freshness, data.recentChanges, data.latestCanvasChange]);
  if (state.contentKey === contentKey) {
    state.data = data; renderConnection(data); window.academicUI?.render(data);
    if ($('#settingsDialog').open && state.settingsTab === 'documents') selectSettingsTab('documents');
    schedulePoll(); return;
  }
  state.contentKey = contentKey;
  const expanded = new Map([...document.querySelectorAll('details[data-preserve]')].map((item) => [item.dataset.preserve, item.open]));
  state.data = data;
  if (data.connection?.native && !data.connection.authorized) {
    searchRequest?.abort(); documentsRequest?.abort(); documentsRevision = null; clearTimeout(searchTimer);
    state.documentSearch = ''; state.searchResults = null;
    $('#documentSearch').value = ''; $('#documentMatches').innerHTML = '';
  }
  $('#appVersion').textContent = `v${data.version || '1.8.2'}`;
  const today = data.today;
  $('#todayLabel').textContent = hkDate(today, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
  $('#syncCaption').textContent = data.lastSync
    ? `Canvas 已连接 · ${data.courseCount} 门课程`
    : '尚未同步 Canvas。点击右上角更新。';
  $('#lastSyncTime').textContent = data.lastSync ? hkLongMoment(data.lastSync) : '尚未同步';
  $('#syncAge').textContent = freshnessText(data);
  $('#freshnessWarnings').textContent = (data.freshness?.warnings || []).join('；');
  $('#confirmedChanges').innerHTML = data.recentChanges?.length
    ? data.recentChanges.slice(0,8).map((row) => `<div class="undated-row"><span>${escapeHtml(row.course || 'Canvas')}</span><div>${anchor(row.url, row.title)}<p class="section-note">${escapeHtml(row.detail)}</p></div></div>`).join('')
    : '<p class="section-note">暂无新的 Canvas 内容变动。</p>';
  const change = data.latestCanvasChange;
  $('#latestContentTime').textContent = change ? hkLongMoment(change.at) : '暂无记录';
  $('#latestContentDetail').innerHTML = change
    ? `${escapeHtml(change.type)} · ${escapeHtml(change.course || 'Canvas')} · ${anchor(change.url, change.title)}`
    : 'Canvas 暂未提供可读取内容的更新时间';
  $('#metricWeek').textContent = String(data.events.filter((event) => event.daysLeft <= 7).length);
  $('#metricWeekRange').textContent = '未来 7 天待处理安排';
  $('#metricCourses').textContent = String(data.courseCount);
  $('#metricUpcoming').textContent = String(data.events.filter((event) => event.daysLeft <= 30).length);
  const exam = data.events.find((event) => ['midterm', 'final', 'test'].includes(event.kind));
  $('#metricExam').textContent = exam ? String(exam.daysLeft).padStart(2, '0') : '—';
  $('#metricExamFoot').textContent = exam ? `${kindLabel(exam.kind)} · ${relative(exam.daysLeft)}` : '暂无已确认日期';
  renderFocus(data);
  renderGrades(data);
  renderUpNext(data);
  renderCoursesFilter(data);
  renderEvents(data);
  renderConnection(data);
  if ($('#settingsDialog').open) {
    if (state.settingsTab === 'documents') selectSettingsTab('documents');
    if (state.settingsTab === 'courses') renderCourses(data);
    if (state.settingsTab === 'logs') refreshLogs();
    document.querySelectorAll('[data-settings-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.settingsPanel !== state.settingsTab || (state.settingsTab === 'account' && !data.connection?.native);
    });
  } else document.querySelectorAll('[data-settings-panel]').forEach((panel) => { panel.hidden = true; });
  document.querySelectorAll('details[data-preserve]').forEach((item) => { if (expanded.has(item.dataset.preserve)) item.open = expanded.get(item.dataset.preserve); });
  window.academicUI?.render(data);
  schedulePoll();
}

function nativeAction(action) {
  const bridge = window.webkit?.messageHandlers?.polyu;
  if (bridge) return bridge.postMessage({ action });
  if (window.pywebview?.api?.native_action) {
    return window.pywebview.api.native_action(action).catch((error) => showNotice(String(error), 'error'));
  }
  showNotice('请在 PolyU study控制台桌面应用中连接 Canvas。', 'error');
}

function renderConnection(data) {
  const connection = data.connection || {};
  if (!connection.native) return;
  $('#storageCaption').textContent = '资料保存在这台电脑';
  const ready = connection.authorized && !connection.needsLogin;
  $('#accountTitle').textContent = ready ? `${connection.name || '你的'} Canvas 已连接` : connection.needsLogin ? 'Canvas 需要重新登录' : '连接你的 PolyU Canvas';
  $('#accountDescription').textContent = ready
    ? '自动遍历课程页面与文件，从原文核对考试日期和评分标准；安排与成绩随之更新。'
    : '在学校窗口登录，授权后逐门读取课程页面、公告、课件文件和已公布成绩。';
  $('#accountStatus').textContent = connection.busy ? connection.stage : connection.lastError || (ready ? '仅在应用打开时自动同步' : '首次连接后读取课程信息');
  $('#accountStatus').classList.toggle('account-error', Boolean(connection.lastError));
  $('#autoSyncToggle').checked = connection.autoSync;
  const interval = connection.syncIntervalSeconds || 3600;
  const period = interval === 3600 ? '每 1 小时' : interval % 60 === 0 ? `每 ${interval / 60} 分钟` : `每 ${interval} 秒`;
  $('#autoSyncLabel').textContent = `应用打开时自动同步（${period}）`;
  $('#loginButton').textContent = ready ? '重新连接 Canvas' : '登录并授权 Canvas';
  $('#loginButton').disabled = connection.busy;
  $('#logoutButton').hidden = !connection.authorized && !connection.needsDataReview;
  $('#logoutButton').disabled = false;
  $('#autoSyncToggle').disabled = connection.busy;
  $('#syncCaption').textContent = ready ? `Canvas 已连接 · ${data.courseCount} 门课程` : '在设置中连接 Canvas，查看学习安排。';
  $('#canvasConnectionCaption').textContent = ready ? '已登录 · 个人学习档案' : '尚未登录';
  $('.connection-light').style.opacity = ready ? '1' : '0.3';
  setBusy(Boolean(connection.busy));
}

let formDirty = false;
document.addEventListener('input', event => { if (event.target.closest('form')) formDirty = true; });
document.addEventListener('close', event => { if (event.target.matches('#eventDialog, #classDialog')) formDirty = false; }, true);
window.polyuPrepareUpdate = () => {
  const openForm = Boolean($('#eventDialog')?.open || $('#classDialog')?.open);
  return !(openForm && formDirty);
};
window.polyuNativeNotice = (message, type = 'info') => showNotice(message, type);
let statusRequest, dataRequest, dataEtag, pollTimer, localRevision, nativeHidden = false, lowPower = false;
window.polyuReload = async (force = false) => {
  if (dataRequest) return;
  const controller = dataRequest = new AbortController();
  try {
    const response = await fetch('/api/data?view=dashboard', {
      cache: 'no-store', signal: controller.signal,
      headers: !force && dataEtag ? { 'If-None-Match': dataEtag } : {}
    });
    if (response.status === 304) return;
    if (!response.ok) { if (response.status === 401) { dataEtag = null; localRevision = null; } return; }
    const data = await response.json();
    if (dataRequest !== controller || !pageVisible()) return;
    dataEtag = response.headers.get('ETag');
    localRevision = data.revision;
    render(data);
  } catch (error) { if (error.name !== 'AbortError') throw error; }
  finally { if (dataRequest === controller) dataRequest = null; }
};
function cancelReadRequests() {
  dataRequest?.abort(); statusRequest?.abort(); documentsRequest?.abort();
  dataRequest = null; statusRequest = null; documentsRequest = null;
  window.academicUI?.cancel();
}
function pageVisible() { return !nativeHidden && !document.hidden; }
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!pageVisible()) return;
  pollTimer = setTimeout(poll, state.data?.connection?.busy || state.calendarBusy ? 2000 : lowPower ? 120000 : 60000);
}
async function poll() {
  if (!pageVisible() || statusRequest) return;
  const controller = statusRequest = new AbortController();
  try {
    const response = await fetch('/api/status', { cache: 'no-store', signal: controller.signal });
    if (response.ok) {
      const status = await response.json();
      if (controller !== statusRequest || !pageVisible()) return;
      state.calendarBusy = status.calendarBusy;
      if (status.revision !== localRevision) {
        await window.polyuReload();
      } else if (state.data) {
        state.data.connection = { ...state.data.connection, ...status.connection };
        renderConnection(state.data);
      }
      if (state.data) $('#syncAge').textContent = freshnessText(state.data);
      window.academicUI?.tick();
    }
  } catch { /* A stopped/restarting backend is retried with the adaptive interval. */ }
  finally { if (statusRequest === controller) statusRequest = null; schedulePoll(); }
}
window.polyuRuntimeState = (value) => {
  const wasVisible = pageVisible();
  nativeHidden = Boolean(value.hidden); lowPower = Boolean(value.lowPower);
  document.body.classList.toggle('low-power', lowPower);
  if (!pageVisible()) { clearTimeout(pollTimer); cancelReadRequests(); }
  else if (!wasVisible) poll();
  else schedulePoll();
};
document.addEventListener('visibilitychange', () => {
  if (pageVisible()) poll(); else { clearTimeout(pollTimer); cancelReadRequests(); }
});


function gradeRaw(score, points) {
  return score == null || points == null ? '—' : `${gradeNumber(score)} / ${gradeNumber(points)}`;
}

function gradeItemMarkup(item) {
  return `<span class="grade-item-chip">${anchor(item.url, item.title)} <b>${escapeHtml(gradeRaw(item.score, item.points))}</b></span>`;
}

function sourcesMarkup(sources, key, context = '') {
  if (!sources?.length && !context) return '';
  return `<details class="evidence-details" data-preserve="${escapeHtml(key)}"><summary>查看详情</summary><div class="evidence-list">${context}${(sources || []).map((source) => `<div class="evidence-source">${anchor(source.url, source.title)}<small>${escapeHtml(source.locator || (source.page ? `第 ${source.page} 页` : 'Canvas 原文'))}${source.lowConfidence ? ' · OCR 待核对' : ''}</small>${source.quote ? `<blockquote>${escapeHtml(source.quote)}</blockquote>` : ''}</div>`).join('')}</div></details>`;
}

function gradeComponentMarkup(component) {
  const label = component.conflict ? '权重冲突' : component.status === 'graded' ? '已折算' : component.status === 'partial' ? '部分出分'
    : component.status === 'submitted' ? '已交待评' : '待出分';
  return `<div class="grade-component-row">
    <div class="grade-component-name"><strong>${escapeHtml(component.name)}</strong><span class="grade-status ${component.status}">${label}</span></div>
    <span>${component.weight == null ? '—' : `${escapeHtml(gradeNumber(component.weight))}%`}</span>
    <span>${escapeHtml(gradeRaw(component.score, component.points))}</span>
    <strong class="grade-component-weighted">${component.weighted == null ? '—' : `${escapeHtml(gradeNumber(component.weighted))}%`}</strong>
    ${component.items.length ? `<div class="grade-component-items">${component.items.map(gradeItemMarkup).join('')}</div>` : ''}
    ${component.conflict ? `<div class="grade-component-warning">原文权重：${component.alternatives.map((w) => `${escapeHtml(gradeNumber(w))}%`).join(' / ')}；折算留空。</div>` : ''}
    ${component.expectedCount ? `<div class="grade-component-warning">文件规定 ${component.expectedCount} 项；已公布 ${component.gradedCount} 项成绩。</div>` : ''}
    ${component.note ? `<div class="grade-component-warning">${escapeHtml(component.note)}</div>` : ''}
    <div class="grade-component-evidence">${sourcesMarkup(component.sources, `grade-evidence:${component.name}:${component.sources?.[0]?.documentId || ''}`)}</div>
  </div>`;
}

function gradeCourseMarkup(course) {
  const hasCanvas = course.canvasCurrentPercent != null;
  const hasWeighted = course.earned != null;
  const canvasPrimary = hasCanvas && course.basis === 'canvas' && !course.conflict && !hasWeighted;
  const status = course.conflict ? '权重待确认' : hasWeighted ? '按权重折算' : canvasPrimary ? 'Canvas 当前成绩' : course.gradedCount ? '有原始分数' : '待出分';
  const raw = !hasWeighted && course.previewRaw;
  const score = hasWeighted ? gradeNumber(course.currentPercent) : canvasPrimary ? gradeNumber(course.canvasCurrentPercent) : raw ? gradeNumber(raw.score) : '—';
  const denominator = hasWeighted || canvasPrimary ? '%' : raw ? ` / ${gradeNumber(raw.points)}` : '';
  const scoreLabel = hasWeighted ? '已评分部分的当前成绩 · 按权重折算' : canvasPrimary ? 'Canvas 已公布的当前成绩' : raw
    ? `${raw.label} · 原始分，折算待定` : '课程折算待出分';
  const components = course.components.length
    ? `<div class="grade-table-head"><span>考核项</span><span>占总评</span><span>Canvas 得分</span><span>折算得分</span></div>${course.components.map(gradeComponentMarkup).join('')}`
    : '<p class="grade-no-scheme">尚未找到课程公布的百分比评分表。</p>';
  const other = course.otherGrades.length
    ? `<div class="grade-unmatched"><span>其他已公布的 Canvas 分数</span><div>${course.otherGrades.map(gradeItemMarkup).join('')}</div></div>` : '';
  return `<details class="grade-course-card" data-preserve="grade:${escapeHtml(course.code)}">
    <summary><div class="grade-card-top"><span class="grade-card-code">${escapeHtml(course.code)}</span><span class="grade-card-status ${course.conflict ? 'conflict' : ''}">${escapeHtml(status)}</span></div>
      <h3>${escapeHtml(course.name)}</h3>
      <div class="grade-card-score"><strong>${escapeHtml(score)}</strong><span>${escapeHtml(denominator)}<small>${escapeHtml(scoreLabel)}</small></span></div>
      <div class="grade-card-bottom"><span>${course.gradedCount ? `${course.gradedCount} 项已出分` : '尚无已公布成绩'}</span><span>查看评分明细 <b aria-hidden="true">⌄</b></span></div>
    </summary>
    <div class="grade-card-detail">
      ${course.source ? `<p class="grade-source">评分标准：${escapeHtml(course.source)}</p>` : ''}
      ${course.note ? `<p class="grade-caveat ${course.conflict ? 'conflict' : ''}">${escapeHtml(course.note)}</p>` : ''}
      ${course.mappingNote ? `<p class="grade-caveat conflict">${escapeHtml(course.mappingNote)}</p>` : ''}
      <div class="grade-component-table">${components}</div>${other}
      ${hasWeighted ? `<p class="grade-subtotal">已评分部分：${escapeHtml(gradeNumber(course.earned))} / ${escapeHtml(gradeNumber(course.assessedWeight))} 权重点（${escapeHtml(gradeNumber(course.currentPercent))}%）。未结算项目未计入。</p>` : '<p class="grade-subtotal muted">课程折算暂为空；未公布的成绩未按零分计。</p>'}
      ${hasCanvas ? `<p class="canvas-grade-reference">Canvas 已公布的当前成绩：${escapeHtml(gradeNumber(course.canvasCurrentPercent))}%（按 Canvas 当前设置显示）</p>` : ''}
    </div>
  </details>`;
}

function renderGrades(data) {
  const grades = data.grades || { recent: [], courses: [] };
  $('#gradeSyncNote').textContent = data.lastSync ? `${data.snapshotComplete ? 'Canvas 资料采集于' : '本轮采集未完整完成，部分结果沿用旧资料；尝试时间'} ${hkLongMoment(data.lastSync)}` : '尚未同步 Canvas 成绩';
  $('#gradeRecentCount').textContent = `最近 ${Math.min(grades.recent.length, 6)} 项`;
  $('#gradeRecentList').innerHTML = grades.recent.length
    ? grades.recent.slice(0, 6).map((item) => `<div class="grade-recent-row">
        <div class="grade-recent-main"><span>${escapeHtml(item.course)} · ${item.at ? escapeHtml(hkMoment(item.at)) : '公布时间未提供'}</span><strong>${anchor(item.url, item.title)}</strong></div>
        <div class="grade-recent-score"><strong>${escapeHtml(gradeRaw(item.score, item.points))}</strong><small>${escapeHtml(gradeNumber(item.score / item.points * 100))}%</small></div>
      </div>`).join('')
    : '<p class="empty-state">Canvas 暂无已公布的数字成绩。</p>';
  const courses = [...grades.courses].sort((a, b) => {
    const aGroup = a.earned != null ? 0 : a.gradedCount ? 1 : 2;
    const bGroup = b.earned != null ? 0 : b.gradedCount ? 1 : 2;
    if (aGroup !== bGroup) return aGroup - bGroup;
    return a.code.localeCompare(b.code);
  });
  $('#gradeCourseGrid').innerHTML = courses.length ? courses.map(gradeCourseMarkup).join('')
    : '<p class="empty-state">尚未读取到课程成绩。</p>';
}

function renderFocus(data) {
  const next = data.events[0];
  if (!next) {
    $('#focusCourse').textContent = '考试与作业';
    const loggedOut = data.connection?.native && !data.connection.authorized;
    $('#focusTitle').textContent = loggedOut ? '登录后查看近期安排' : '暂无有日期的近期安排';
    $('#focusReason').textContent = loggedOut ? '连接 Canvas 后读取课程和文件。' : '可查看日期待核对的事项，或更新 Canvas。';
    $('#focusStep').textContent = loggedOut ? '打开右上角「设置」连接 Canvas' : '查看考试与作业安排';
    $('#focusLink').hidden = true;
    $('#focusLink').removeAttribute('href');
    return;
  }
  $('#focusCourse').textContent = next.course || 'Canvas';
  $('#focusTitle').textContent = next.title || '近期安排';
  $('#focusReason').textContent = `${hkDate(next.date)} · ${relative(next.daysLeft)} · ${kindLabel(next.kind)}`;
  $('#focusStep').textContent = next.timeText || next.time || '具体时间待核对';
  const href = validLink(next.url);
  $('#focusLink').hidden = !href;
  if (href) $('#focusLink').href = href;
  else $('#focusLink').removeAttribute('href');
}

function renderUpNext(data) {
  const list = $('#upNextList');
  if (!data.events.length) {
    list.innerHTML = '<p class="empty-state">近期没有有日期的未完成安排。</p>';
    return;
  }
  list.innerHTML = data.events.slice(0, 4).map((event) => `
    <div class="up-next-row">
      <div class="date-tile"><strong>${escapeHtml(event.date.slice(8, 10))}</strong><span>${escapeHtml(hkDate(event.date, { month: 'short' }))}</span></div>
      <div class="up-next-content"><span class="eyeline">${escapeHtml(event.course)} <b>·</b> ${escapeHtml(kindLabel(event.kind))}</span>
        ${anchor(event.url, event.title, 'up-next-title')}</div>
      <span class="days-away">${escapeHtml(relative(event.daysLeft))}</span>
    </div>`).join('');
}

function renderCoursesFilter(data) {
  const select = $('#courseFilter');
  const value = state.course;
  select.innerHTML = '<option value="all">全部课程</option>' + data.courses.map((course) =>
    `<option value="${escapeHtml(course.code)}">${escapeHtml(course.code)}</option>`).join('');
  if ([...select.options].some((option) => option.value === value)) select.value = value;
  else { select.value = 'all'; state.course = 'all'; }
  const formSelect = $('#eventForm select[name="course"]');
  const formValue = formSelect.value;
  formSelect.innerHTML = '<option value="">请选择课程</option>' + data.courses.map((course) =>
    `<option value="${escapeHtml(course.code)}">${escapeHtml(course.code)} · ${escapeHtml(course.name)}</option>`).join('');
  if (formValue) formSelect.value = formValue;
}

function eventMarkup(event) {
  const className = `kind-${event.kind}`;
  const dateLabel = `${hkDate(event.date)}${event.timeText && event.time ? ` · ${event.timeText}` : event.time ? ` · ${event.time}` : ''}`;
  const source = event.sourceLabel || 'Canvas';
  const weight = !event.weight || event.weight === '—' ? '' : /^\d/.test(event.weight)
    ? ` · 占比 ${escapeHtml(event.weight)}` : ` · 权重${escapeHtml(event.weight)}`;
  const warnings = (event.warnings || []).filter((line) => line !== historicalNotice);
  const context = `<p class="event-source">${escapeHtml(source)}${weight}</p>` + (event.warnings || []).filter((line) => line === historicalNotice).map((line) => `<p class="event-note">${escapeHtml(line)}</p>`).join('');
  return `<article class="event-row ${event.pending ? 'pending-event' : ''}">
    <div class="event-date"><strong>${escapeHtml(event.date.slice(8, 10))}</strong><span>${escapeHtml(hkDate(event.date, { month: 'short' }))}</span></div>
    <div class="event-main"><div class="event-meta"><span class="kind-badge ${className}">${escapeHtml(kindLabel(event.kind))}</span><span>${escapeHtml(event.course)}</span><span class="meta-divider">·</span><span>${escapeHtml(dateLabel)}</span></div>
      <h3>${anchor(event.url, event.title)}</h3>
      ${event.note ? `<p class="event-note">${escapeHtml(event.note)}</p>` : ''}
      ${event.location ? `<p class="event-location">地点：${escapeHtml(event.location)}</p>` : ''}
      ${event.coverage?.length ? `<p class="event-note">范围：${escapeHtml(event.coverage.join('；'))}</p>` : ''}
      ${event.instructions?.length ? `<details class="event-instructions" data-preserve="instructions:${escapeHtml(event.id)}"><summary>考试要求</summary>${event.instructions.map((line) => `<p>${escapeHtml(line)}</p>`).join('')}</details>` : ''}
      ${warnings.map((line) => `<p class="event-note">${escapeHtml(line)}</p>`).join('')}
      ${event.timeText && !event.time ? `<p class="event-note">${escapeHtml(event.timeText)}</p>` : ''}
      ${sourcesMarkup(event.sources, `event:${event.id}`, context)}
    </div>
    <div class="event-right"><span class="event-remaining">${escapeHtml(relative(event.daysLeft))}</span>
      ${event.editable ? `<div class="row-actions"><button type="button" data-action="edit" data-id="${escapeHtml(event.id)}">编辑</button><button type="button" data-action="delete" data-id="${escapeHtml(event.id)}">删除</button></div>` : '<span class="canvas-source">CANVAS</span>'}
    </div>
  </article>`;
}

function renderEvents(data) {
  const rows = data.events.filter((event) =>
    (state.filter === 'all' || event.kind === state.filter) &&
    (state.course === 'all' || event.course === state.course));
  $('#eventList').innerHTML = rows.length
    ? rows.map(eventMarkup).join('')
    : '<div class="empty-state roomy">这个筛选下暂无近期安排。</div>';
  const pendingRows = data.undated.filter((event) => (state.filter === 'all' || event.kind === state.filter) && (state.course === 'all' || event.course === state.course));
  $('#undatedCount').textContent = String(pendingRows.length);
  $('#undatedList').innerHTML = pendingRows.length
    ? pendingRows.map((event) => `<div class="undated-row"><span>${escapeHtml(event.course)}</span><div class="pending-copy">${anchor(event.url, event.title)}${event.reason ? `<p>${escapeHtml(event.reason)}</p>` : ''}${sourcesMarkup(event.sources, `pending:${event.id}`)}</div></div>`).join('')
    : '<p class="empty-state">没有待确认日期的事项。</p>';
}

function renderDocuments(data) {
  const docs = data.documents || {};
  const busy = docs.scanning || data.connection?.busy;
  $('#documentStatus').textContent = busy ? '正在自动识别' : !docs.completedAt ? '等待采集' : docs.reviewCount ? '已提取 · 部分来源冲突' : docs.complete ? '文档识别完成' : '已提取 · 部分读取未完成';
  $('#documentStatus').className = `document-status ${docs.reviewCount ? 'review' : docs.complete ? 'complete' : ''}`;
  $('#documentDownloadCount').textContent = `${docs.downloadedFiles || 0} / ${docs.downloadFileCount || 0}`;
  $('#documentFileCount').textContent = `${docs.readFiles || 0} / ${docs.fileCount || 0}`;
  $('#documentPageCount').textContent = `${docs.processedPages || 0} / ${docs.pageCount || 0}`;
  $('#documentFindingCount').textContent = String(docs.findingCount || 0);
  $('#documentReviewCount').textContent = String(docs.reviewCount || 0);
  $('#documentDownloadReport').textContent = docs.downloadedAt
    ? `${docs.downloadComplete ? 'Canvas 模块中可见附件已全部下载。' : '附件下载尚未全部完成，清单中的失败项会在下次登录同步时重试。'} ${docs.downloadedFiles || 0} / ${docs.downloadFileCount || 0} 个文件；完成时间 ${hkLongMoment(docs.downloadedAt)}。下载目录位于本机当前账号的 documents/files。`
    : '登录后先按课程与 Canvas 模块下载附件，下载阶段结束后才开始逐份识别。';
  if (docs.downloadIssues?.length) $('#documentDownloadReport').textContent += ` 范围提示：${docs.downloadIssues.join('；')}`;
  if (docs.revalidatedFiles) $('#documentDownloadReport').textContent += ` ${docs.revalidatedFiles} 个附件经服务器确认未变化，复用本机文件。`;
  const grouped = new Map();
  for (const entry of docs.downloadEntries || []) for (const folder of entry.folders || ['未命名单元']) {
    const key = `${entry.course} / ${folder}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(entry);
  }
  $('#documentDownloadTree').innerHTML = grouped.size ? [...grouped].map(([folder, entries]) => `<div class="document-row"><div><h4>${escapeHtml(folder)}</h4>${entries.map((entry) => `<p>${anchor(entry.url, entry.title)} · ${entry.status === 'downloaded' ? '已下载' : '下载失败'}${entry.issues?.length ? ` · ${escapeHtml(entry.issues.join('；'))}` : ''}</p>`).join('')}</div><span class="document-row-label ${entries.every((e) => e.status === 'downloaded') ? 'read' : ''}">${entries.filter((e) => e.status === 'downloaded').length}/${entries.length}</span></div>`).join('') : '<p class="empty-state">尚无已归档的模块附件。</p>';
  const phaseLabels = { running: '进行中', complete: '已完成', partial: '部分未完成' };
  $('#syncPhases').innerHTML = (data.connection?.syncStatus?.phases || []).map((p) => `<details class="sync-phase ${escapeHtml(p.status)}" data-preserve="phase:${escapeHtml(p.name)}"><summary>${escapeHtml(p.name)}<span>${phaseLabels[p.status] || '等待中'}</span></summary>${p.detail ? `<p>${escapeHtml(p.detail)}</p>` : '<p>同步时自动更新。</p>'}</details>`).join('') + (docs.integrationIssue ? `<p class="sync-phase-error">${escapeHtml(docs.integrationIssue)}</p>` : '');
  $('#documentProgress').textContent = busy ? data.connection?.stage || '正在读取课程文件…' : docs.completedAt
    ? `上次文档识别：${hkLongMoment(docs.completedAt)}。${docs.sourceMode === 'cached' ? '已处理此前下载的文件；Canvas 会话过期，新资料尚未同步。' : '更新时重新遍历课程，自动下载并识别新建或修改的文件。'}${docs.issueCount ? ` ${docs.issueCount} 处内容暂不可读，详情见文档清单。` : ''}`
    : '登录后自动扫描你的课程资料；首次读取全部课件可能需要较长时间。';
  $('#documentCourseSummary').innerHTML = (docs.courses || []).map((course) => {
    const files = (docs.records || []).filter((r) => r.course === course.code && r.type === 'file');
    const found = files.reduce((n, r) => n + (r.findings?.length || 0), 0);
    return `<span class="document-course-chip" title="${escapeHtml((course.issues || []).join('；'))}"><b>${escapeHtml(course.code)}</b>${files.filter((r) => r.status === 'read').length} / ${files.length} 文件 · 提取 ${found} 项</span>`;
  }).join('');
  const evidence = docs.evidence || {};
  const codes = [...new Set([...(evidence.deadlines || []).map((r) => r.course), ...Object.keys(evidence.grading || {})])];
  $('#documentInsights').innerHTML = codes.map((code) => {
    const dates = (evidence.deadlines || []).filter((e) => e.course === code && e.date >= data.today);
    const weights = evidence.grading?.[code] || [];
    return `<article class="document-insight-card"><p class="section-kicker">${escapeHtml(code)}</p>
      ${dates.map((e) => `<div class="document-insight-event"><b>${escapeHtml(e.title)}</b><span>${escapeHtml(hkDate(e.date))}${e.timeText ? ` · ${escapeHtml(e.timeText)}` : ''}${e.location ? ` · ${escapeHtml(e.location)}` : ''}</span>${sourcesMarkup(e.sources, `insight:${e.id}`)}</div>`).join('')}
      ${weights.length ? `<h4>自动提取的评分细则</h4><div class="document-weight-chips">${weights.map((w) => `<span class="${w.conflict ? 'conflict' : ''}">${escapeHtml(w.name)} <b>${w.weight != null ? `${w.weight}%` : (w.alternatives || []).map((v) => `${v}%`).join(' / ') + ' · 冲突'}</b></span>`).join('')}</div>` : ''}
    </article>`;
  }).join('') || '<p class="empty-state">文档中的考试、作业和评分信息会在这里自动汇总。</p>';
  renderDocumentList();
}

function renderDocumentList() {
  const docs = state.data?.documents || {};
  const labels = { read: '已识别', cached: '沿用上次结果 · 等待复核', analyzing: '正文已读 · 图片识别中', partial: '部分内容待重试', locked: '尚未开放', unsupported: '格式暂未支持', failed: '自动读取待重试' };
  const kinds = { file: '文件', syllabus: '课程提纲', page: '课程页面', assignment: '作业说明', announcement: '公告', quiz: '测验说明', external: '外部教学工具' };
  const search = state.documentSearch.trim().toLocaleLowerCase();
  const rows = (docs.records || []).filter((r) => (!search || `${r.course} ${r.title} ${JSON.stringify(r.findings || [])}`.toLocaleLowerCase().includes(search)) &&
    (state.documentFilter === 'all' || (state.documentFilter === 'insights' ? r.findings?.length : state.documentFilter === 'read' ? r.status === 'read' : r.status !== 'read')));
  const scopeIssues = (docs.courses || []).flatMap((course) => (course.issues || []).map((issue) => `<div class="document-row"><div><small>${escapeHtml(course.code)} · 遍历范围</small><h4>${escapeHtml(issue)}</h4></div><span class="document-row-label">列表不完整</span></div>`));
  $('#documentList').innerHTML = (!['read', 'insights'].includes(state.documentFilter) ? scopeIssues.join('') : '') + (rows.length ? rows.map((row) => `<div class="document-row"><div><small>${escapeHtml(row.course)} · ${kinds[row.type] || '课程内容'}</small><h4>${anchor(row.url, row.title)}</h4><small>${row.pageCount ? `${row.processedPages || row.readPages} / ${row.pageCount} 页 / 位置 · ` : ''}${escapeHtml((row.methods || []).join('、'))}</small>${(row.findings || []).map((f, i) => `<div class="document-file-finding"><b>${escapeHtml(f.title)}</b><span>${escapeHtml([...(f.dates || []).map((v) => hkDate(v)), ...(f.weights || []).map((v) => `${v}%`), ...(f.details || [])].join(' · '))}</span>${sourcesMarkup(f.sources, `file-finding:${row.id}:${i}`)}</div>`).join('')}${!row.findings?.length && row.status === 'read' ? '<p class="document-no-finding">已识别正文与图片；未发现明确的考试安排或评分细则。</p>' : ''}${(row.issues || []).map((issue) => `<p>${escapeHtml(issue)}</p>`).join('')}</div><span class="document-row-label ${row.status === 'read' ? 'read' : ''}">${labels[row.status] || '等待识别'}</span></div>`).join('') : '<p class="empty-state">暂无符合筛选条件的资料。</p>');
}

async function searchDocumentText() {
  const query = state.documentSearch.trim();
  searchRequest?.abort();
  state.searchResults = null;
  if (!query || (state.data?.connection?.native && !state.data.connection.authorized)) { $('#documentMatches').innerHTML = ''; return; }
  const request = searchRequest = new AbortController();
  $('#documentMatches').textContent = '正在搜索当前账号已读取的本机正文…';
  try {
    const response = await fetch(`/api/documents/search?q=${encodeURIComponent(query)}`, { signal: request.signal, cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '搜索未完成');
    if (request !== searchRequest || query !== state.documentSearch.trim()) return;
    state.searchResults = data;
    $('#documentMatches').innerHTML = `<p class="section-note">仅搜索本账号已缓存的正文与 OCR 文本；未读取内容不会出现在正文结果中。${data.truncated ? '结果已达本次搜索上限，请缩小搜索词。' : ''}${data.scanning ? '资料识别仍在进行。' : ''}</p>` + (data.results.length ? data.results.map((row) => `<div class="document-row"><div><small>${escapeHtml(row.course)} · ${escapeHtml(row.locator)}</small><h4>${anchor(row.url, row.title)}</h4><p class="search-excerpt">${escapeHtml(row.excerpt)}</p><small>读取时间：${escapeHtml(hkLongMoment(row.readAt))}${row.status === 'cached' ? ' · 沿用旧结果，等待复核' : ''}</small></div></div>`).join('') : '<p class="empty-state">当前已缓存正文中没有匹配结果。</p>');
  } catch (error) {
    if (error.name !== 'AbortError' && request === searchRequest) $('#documentMatches').textContent = error.message;
  }
}


function renderCourses(data) {
  const sorted = [...data.courses].sort((a, b) => a.code.localeCompare(b.code));
  $('#courseGrid').innerHTML = sorted.map((course, index) => {
    const next = data.events.find((event) => event.course === course.code);
    const href = `https://canvas.polyu.edu.hk/courses/${encodeURIComponent(course.id)}/modules`;
    return `<article class="course-card"><div class="course-card-top"><span class="course-index">${String(index + 1).padStart(2, '0')}</span><span class="course-code">${escapeHtml(course.code)}</span></div>
      <h3>${escapeHtml(course.name)}</h3><p class="course-next">${next ? `下一项：${escapeHtml(next.title)} · ${escapeHtml(hkDate(next.date))}` : '暂无已确认日期的近期安排'}</p>
      <a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" class="course-open">打开 Canvas 课程 <span aria-hidden="true">↗</span></a></article>`;
  }).join('') || '<p class="empty-state">登录并同步后，这里显示你的课程。</p>';
}

function openEventDialog(event = null) {
  if (state.busy || !state.data) return;
  const form = $('#eventForm');
  form.reset();
  state.editOriginal = event ? { course: event.course, title: event.rawTitle } : null;
  $('#dialogTitle').textContent = event ? '编辑安排' : '添加安排';
  form.elements.course.disabled = !!event;
  form.elements.title.readOnly = !!event;
  if (event) {
    form.elements.course.value = event.course;
    form.elements.title.value = event.rawTitle;
    form.elements.date.value = event.date;
    form.elements.time.value = event.time || '';
    form.elements.kind.value = event.kind || '';
    form.elements.weight.value = /^\d/.test(event.weight || '') ? event.weight : '';
    form.elements.url.value = event.url || '';
    form.elements.note.value = event.note || '';
  } else {
    form.elements.date.value = state.data.today;
  }
  $('#eventDialog').showModal();
  (event ? form.elements.date : form.elements.course).focus();
}

async function submitEvent(event) {
  event.preventDefault();
  if (state.busy) return;
  const form = event.currentTarget;
  const payload = {
    course: form.elements.course.value, title: form.elements.title.value.trim(), date: form.elements.date.value,
    time: form.elements.time.value, weight: form.elements.weight.value.trim(),
    kind: form.elements.kind.value,
    url: form.elements.url.value.trim(), note: form.elements.note.value.trim(), original: state.editOriginal
  };
  if (!payload.course || !payload.title || !payload.date) {
    showNotice('请填写课程、事项名称和日期。', 'error'); return;
  }
  if (state.data?.connection?.native && !validLink(payload.url)) { showNotice('请填写这项安排的 Canvas 来源链接。', 'error'); return; }
  setBusy(true);
  showNotice('正在保存安排…', 'info');
  try {
    const result = await api('/api/event/save', payload);
    $('#eventDialog').close();
    showNotice(result.message, 'success');
  } catch (error) {
    showNotice(error.message, 'error');
  } finally { setBusy(false); }
}

async function refresh() {
  if (state.busy) return;
  setBusy(true);
  showNotice('正在更新 Canvas；识别进度可在设置中查看。', 'info');
  try {
    const result = await api('/api/refresh', {});
    showNotice(result.message, 'success');
  } catch (error) {
    showNotice(error.message, 'error');
  } finally { setBusy(Boolean(state.data?.connection?.busy)); }
}


async function deleteEvent(event) {
  if (state.busy || !confirm(`删除「${event.rawTitle}」的手动安排？`)) return;
  setBusy(true);
  showNotice('正在删除安排…', 'info');
  try {
    const result = await api('/api/event/delete', { course: event.course, title: event.rawTitle });
    showNotice(result.message, 'success');
  } catch (error) {
    showNotice(error.message, 'error');
  } finally { setBusy(false); }
}

document.addEventListener('DOMContentLoaded', async () => {
  document.addEventListener('click', (event) => {
    const source = event.target.closest('a[target="_blank"]');
    if (source && window.pywebview?.api?.native_action && validLink(source.href)) {
      event.preventDefault();
      window.pywebview.api.native_action('openSource', source.href).catch((error) => showNotice(String(error), 'error'));
    }
  });
  $('#refreshButton').addEventListener('click', refresh);
  $('#settingsButton').addEventListener('click', (event) => openSettings(state.settingsTab, event.currentTarget));
  $('#coursesNav').addEventListener('click', (event) => { event.preventDefault(); openSettings('courses', event.currentTarget); });
  $('#closeSettingsButton').addEventListener('click', () => $('#settingsDialog').close());
  $('#settingsDialog').addEventListener('close', settingsClosed);
  document.querySelectorAll('[data-settings-tab]').forEach((button) => {
    button.addEventListener('click', () => selectSettingsTab(button.dataset.settingsTab));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...document.querySelectorAll('[data-settings-tab]')];
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : (tabs.indexOf(button) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectSettingsTab(tabs[index].dataset.settingsTab, true);
    });
  });
  $('#refreshLogsButton').addEventListener('click', refreshLogs);
  $('#logFilter').addEventListener('change', (event) => { state.logFilter = event.target.value; renderLogs(); });
  $('#clearLogsButton').addEventListener('click', async () => {
    const button = $('#clearLogsButton'); button.disabled = true;
    try { const result = await api('/api/diagnostics/clear'); showNotice(result.message, 'success'); await refreshLogs(); }
    catch (error) { showNotice(error.message, 'error'); }
    finally { button.disabled = false; }
  });
  $('#loginButton').addEventListener('click', () => nativeAction('login'));
  $('#logoutButton').addEventListener('click', () => nativeAction('logout'));
  $('#dataFolderButton').addEventListener('click', () => nativeAction('dataFolder'));
  $('#documentFolderButton').addEventListener('click', () => nativeAction('filesFolder'));
  $('#autoSyncToggle').addEventListener('change', async (event) => {
    try { const result = await api('/api/settings', { autoSync: event.target.checked }); showNotice(result.message, 'success'); }
    catch (error) { showNotice(error.message, 'error'); }
  });
  $('#addEventButton').addEventListener('click', () => openEventDialog());
  $('#eventForm').addEventListener('submit', submitEvent);
  $('#closeDialog').addEventListener('click', () => $('#eventDialog').close());
  $('#cancelDialog').addEventListener('click', () => $('#eventDialog').close());
  $('#courseFilter').addEventListener('change', (event) => { state.course = event.target.value; renderEvents(state.data); });
  $('#documentSearch').addEventListener('input', (event) => {
    state.documentSearch = event.target.value; renderDocumentList();
    searchRequest?.abort(); documentsRequest?.abort(); documentsRevision = null; clearTimeout(searchTimer);
    searchTimer = setTimeout(searchDocumentText, 250);
  });
  $('#documentFilter').addEventListener('change', (event) => { state.documentFilter = event.target.value; renderDocumentList(); });
  document.querySelectorAll('.segmented button').forEach((button) => button.addEventListener('click', () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll('.segmented button').forEach((item) => {
      item.classList.toggle('selected', item === button);
      item.setAttribute('aria-selected', String(item === button));
    });
    renderEvents(state.data);
  }));
  $('#eventList').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const item = state.data.events.find((row) => row.id === button.dataset.id);
    if (!item) return;
    if (button.dataset.action === 'edit') openEventDialog(item);
    if (button.dataset.action === 'delete') deleteEvent(item);
  });
  window.addEventListener('hashchange', () => {
    if (location.hash === '#courses') { history.replaceState(null, '', '#top'); openSettings('courses', $('#coursesNav')); }
    else if (location.hash === '#documents') { history.replaceState(null, '', '#top'); openSettings('documents', $('#settingsButton')); }
    updateActiveNav();
  });
  updateActiveNav();
  schedulePoll();
  window.academicUI?.init();
  try {
    const bootstrap = new URLSearchParams(location.hash.slice(1)).get('bootstrap');
    if (bootstrap) {
      // Fragments never reach HTTP logs; remove the single-use ticket immediately.
      history.replaceState(null, '', location.pathname);
      const authorization = await fetch('/api/client/session', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticket: bootstrap }), cache: 'no-store', credentials: 'same-origin'
      });
      if (!authorization.ok) throw new Error('客户端授权已失效，请重新打开控制台');
      csrf = (await authorization.json()).csrf;
    }
    const response = await fetch('/api/data?view=dashboard', { cache: 'no-store' });
    if (!response.ok) throw new Error('读取学习档案失败');
    dataEtag = response.headers.get('ETag');
    localRevision = data.revision;
    render(await response.json());
    if (['#courses', '#documents'].includes(location.hash)) {
      const tab = location.hash === '#courses' ? 'courses' : 'documents'; history.replaceState(null, '', '#top'); openSettings(tab);
    }
  } catch (error) {
    showNotice(`无法读取本机学习档案：${error.message}`, 'error');
  }
});
