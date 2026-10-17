/* Timetable and public academic calendar share the dashboard revision clock. */
(() => {
  const ui = { date: '', month: '', year: '', view: 'day', course: '', data: null, timetable: null, calendar: null, token: 0, requests: [], edit: null };
  const e = escapeHtml;
  const shift = (value, days) => { const d = new Date(value + 'T12:00:00+08:00'); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };
  const today = () => state.data?.today || dateFormatter('en-CA', { timeZone: 'Asia/Hong_Kong', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const kindName = value => ({ lecture: '讲课', lab: '实验', tutorial: '辅导课', final: '期末考试', midterm: '期中考试', test: '测验', exam: '考试（未分类）' }[value] || '课项');
  function sourceHtml(sources) {
    return (sources || []).map(source => '<div class="evidence-source">' + anchor(source.url, source.title || 'Canvas 来源') + '<small>' + e(source.locator || '') + ' · ' + e(source.method || '') + (source.confidence < .75 ? ' · 置信度不足' : '') + '</small><blockquote>' + e(source.quote || '') + '</blockquote></div>').join('');
  }
  function cancel() { ui.token++; ui.requests.forEach(r => r.abort()); ui.requests = []; }
  async function read(path, token) {
    const request = new AbortController(); ui.requests.push(request);
    const response = await fetch(path, { cache: 'no-store', signal: request.signal });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || '读取未完成');
    if (token !== ui.token || !pageVisible()) throw new DOMException('读取已取消', 'AbortError');
    return value;
  }
  async function refresh(fullPending = false) {
    if (!pageVisible() || !ui.data) return;
    cancel(); const token = ui.token;
    const paths = ['/api/timetable?' + new URLSearchParams({ date: ui.date, view: ui.view, ...(!fullPending ? { summary: '1' } : {}), ...(ui.course ? { course: ui.course } : {}) }), '/api/calendar?' + new URLSearchParams({ month: ui.month, ...(ui.year ? { year: ui.year } : {}) })];
    const results = await Promise.allSettled(paths.map(path => read(path, token)));
    if (token !== ui.token || !pageVisible()) return;
    results.forEach((result, i) => {
      if (result.status === 'fulfilled') { if (i === 0) { ui.timetable = result.value; renderTimetable(); } else { ui.calendar = result.value; renderCalendar(); } }
      else if (result.reason.name !== 'AbortError') $(i === 0 ? '#timetableStatus' : '#calendarStatus').textContent = result.reason.message;
    });
  }
  function card(row) {
    return '<article class="class-item' + (row.occurrenceCancelled ? ' cancelled' : '') + (row.overlaps?.length ? ' overlap' : '') + '"><div class="class-time">' + e(row.time || '时间未公布') + (row.endTime ? '–' + e(row.endTime) : '') + '</div><div><span class="class-course">' + e(row.course) + ' · ' + kindName(row.kind) + '</span><strong>' + e(row.title) + '</strong><small>' + e([row.group ? '班组 ' + row.group : '', row.location].filter(Boolean).join(' · ')) + '</small>' + (row.occurrenceCancelled ? '<p class="class-warning">此日停课</p>' : '') + (row.overlaps?.length ? '<p class="class-warning">时间重叠，请核对</p>' : '') + (row.calendarHints?.length ? '<p class="class-warning">' + e(row.calendarHints.map(h => (h.scope === 'evening' ? '晚间停课提示：' : '停课提示：') + h.title).join('；')) + '</p>' : '') + '<button class="text-link class-detail" type="button" data-class-detail="' + e(row.id) + '">详情 / 调整</button></div></article>';
  }
  function renderTimetable() {
    const value = ui.timetable || {};
    $('#timetableDate').textContent = ui.view === 'week' && value.days?.length ? hkDate(value.days[0]) + ' — ' + hkDate(value.days[6]) : hkDate(ui.date);
    document.querySelectorAll('[data-class-view]').forEach(b => { const on = b.dataset.classView === ui.view; b.classList.toggle('selected', on); b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
    const events = value.events || [];
    $('#timetableBody').className = ui.view === 'week' ? 'class-week' : 'class-day';
    $('#timetableBody').closest('.class-scroll').classList.toggle('is-week', ui.view === 'week');
    if (ui.view === 'week' && value.days?.length) {
      $('#timetableBody').innerHTML = value.days.map(date => '<div class="class-column"><h3>' + e(hkDate(date, { month: 'numeric', day: 'numeric', weekday: 'short' })) + '</h3>' + (events.filter(row => row.date === date).map(card).join('') || '<p class="class-empty">暂无已确认课项</p>') + '</div>').join('');
    } else $('#timetableBody').innerHTML = events.map(card).join('') || '<p class="class-empty">' + (ui.data?.connection?.authorized ? '本日暂无已确认的上课或考试安排。' : '登录 Canvas 后显示个人课程表。') + '</p>';
    if (value.eventsTotal > events.length) $('#timetableBody').innerHTML += '<button class="text-link" type="button" data-class-all-pending>查看全部课项</button>';
    const pending = value.pending || [];
    $('#timetablePending').innerHTML = pending.length ? '<summary>待确认 ' + (value.pendingTotal ?? pending.length) + ' 项</summary>' + pending.map(row => '<div class="class-candidate"><strong>' + e(row.course + ' · ' + row.title) + '</strong><small>' + e((row.issues || []).join('；')) + '</small><button class="text-link" type="button" data-class-detail="' + e(row.id) + '">核对 / 确认</button></div>').join('') : '';
    if (value.pendingTotal > pending.length) $('#timetablePending').innerHTML += '<button class="text-link" type="button" data-class-all-pending>查看全部待确认课项</button>';
    $('#timetablePending').hidden = !pending.length;
    $('#timetableStatus').textContent = [...(value.issues || []).map(v => v.message), ...(value.conflicts || []).map(v => v.title + '：' + v.message)].join('；');
    $('#addClassButton').disabled = !ui.data?.connection?.authorized || !ui.data?.courses?.length || Boolean(ui.data?.connection?.busy);
    $('#timetableCourse').disabled = !ui.data?.courses?.length;
  }
  function renderCalendar() {
    const value = ui.calendar;
    if (!value) return;
    $('#calendarYear').innerHTML = (value.years || []).map(year => '<option value="' + e(year) + '"' + (year === value.year ? ' selected' : '') + '>' + e(year) + '</option>').join('');
    $('#calendarMonth').textContent = ui.month.replace('-', '年 ') + '月';
    const first = ui.month + '-01', d = new Date(first + 'T12:00:00+08:00');
    const blank = (d.getUTCDay() + 6) % 7;
    const count = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
    const personal = [...(ui.data?.events || []), ...(ui.data?.overdue || [])];
    const selectedInMonth = ui.date?.startsWith(ui.month);
    const focusDate = selectedInMonth ? ui.date : today().startsWith(ui.month) ? today() : first;
    let html = Array.from({ length: blank }, () => '<span aria-hidden="true"></span>').join('');
    for (let n = 1; n <= count; n++) {
      const date = ui.month + '-' + String(n).padStart(2, '0');
      const hints = (value.events || []).filter(row => row.start <= date && date <= row.end);
      const arrangements = personal.filter(row => row.date === date);
      html += '<button type="button" data-calendar-date="' + date + '" class="' + (date === ui.date ? 'selected ' : '') + (date === today() ? 'today ' : '') + (arrangements.length ? 'has-arrangements ' : '') + (hints.some(h => h.scope === 'all') ? 'holiday' : hints.length ? 'marked' : '') + '" tabindex="' + (date === focusDate ? '0' : '-1') + '" aria-pressed="' + (date === ui.date) + '"' + (date === today() ? ' aria-current="date"' : '') + ' aria-label="' + e(hkDate(date, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }) + (arrangements.length ? '，' + arrangements.length + ' 项个人安排' : '') + (hints.length ? '，' + hints.map(h => h.title).join('；') : '')) + '">' + n + (arrangements.length ? '<i class="personal-date-dot" aria-hidden="true"></i>' : '') + '</button>';
    }
    const focusedDate = document.activeElement?.dataset?.calendarDate;
    if (!ui.calendarFocus && focusedDate) ui.calendarFocus = focusedDate;
    $('#calendarDays').innerHTML = html;
    if (ui.calendarFocus) {
      const button = document.querySelector('[data-calendar-date="' + ui.calendarFocus + '"]');
      if (button) button.focus({ preventScroll: true });
      ui.calendarFocus = null;
    }
    $('#selectedDateTitle').textContent = hkDate(ui.date, { month: 'long', day: 'numeric' }) + ' · 个人安排';
    const selected = personal.filter(row => row.date === ui.date);
    $('#selectedDateEvents').innerHTML = selected.length ? selected.map(row => '<div class="calendar-agenda-item"><span>' + e(row.course) + ' · ' + e(kindLabel(row.kind)) + '</span><strong>' + anchor(row.url, row.title) + '</strong><small>' + e(row.timeText || row.time || '具体时间待核对') + (row.overdueState ? ' · 已过截止日期，请核对状态' : '') + '</small></div>').join('') : '<p class="calendar-agenda-empty">' + (ui.data?.connection?.native && !ui.data.connection.authorized ? '连接 Canvas 后查看个人安排。' : '本日暂无已读取的考试或作业。') + '</p>';
    $('#calendarWeek').textContent = value.teachingWeek ? '当前：学期 ' + value.teachingWeek.semester + ' · 第 ' + value.teachingWeek.number + ' 教学周' : '当前为非教学周 / 学年外';
    $('#calendarDatesTitle').textContent = '本月重要日期 · ' + (value.events || []).length + ' 项';
    $('#calendarEvents').innerHTML = (value.events || []).map(row => '<li><span>' + e(row.start.slice(5) + (row.end !== row.start ? ' — ' + row.end.slice(5) : '')) + '</span><p>' + e(row.title) + (row.tentative ? '（暂定）' : '') + (row.scope === 'evening' ? ' · 仅晚间停课' : '') + '</p></li>').join('') || '<li>本月暂无校历事项</li>';
    $('#calendarStatus').textContent = value.issue || (value.busy ? '正在检查官方修订…' : '检查：' + hkMoment(value.checkedAt) + (value.updatedAt ? ' · 修订：' + value.updatedAt : ''));
    $('#calendarRefresh').disabled = Boolean(value.busy);
  }
  function formRow(row) {
    ui.edit = row || null;
    const form = $('#classForm'); form.reset();
    const set = (name, value) => { if (form.elements[name]) form.elements[name].value = value ?? ''; };
    const repeat = row?.repeat || {};
    set('courseId', row?.courseId || ui.data?.courses?.[0]?.id); set('kind', row?.kind || 'lecture'); set('title', row?.title);
    set('date', row ? row.dates?.[0] || row.date || '' : ui.date); set('group', row?.group); set('time', row?.time); set('endTime', row?.endTime); set('location', row?.location);
    set('mode', row?.repeat ? 'repeat' : 'single'); set('start', repeat.start || ui.date); set('end', repeat.end); set('parity', repeat.parity || 'all'); set('basis', repeat.basis || 'anchor'); set('anchor', repeat.anchor);
    set('dates', (row?.dates || []).slice(1).join(','));
    set('semester', repeat.semester || '1'); set('weeks', (repeat.weeks || []).join(',')); set('exceptions', (row?.exceptions || []).join(','));
    form.querySelectorAll('[name="weekday"]').forEach(b => { b.checked = (repeat.weekdays || []).includes(Number(b.value)); });
    form.elements.cancelled.checked = Boolean(row?.cancelled); form.elements.makeup.checked = Boolean(row?.makeup);
    $('#classRepeatFields').hidden = form.elements.mode.value !== 'repeat';
    $('#classEvidence').innerHTML = sourceHtml(row?.sources);
    $('#classDialogTitle').textContent = row ? '核对课程表安排' : '添加上课 / 考试';
    $('#classHide').hidden = !row || Boolean(row.manual);
    $('#classDelete').hidden = !row?.manual;
    $('#classGroup').hidden = !row?.group || Boolean(row.manual);
    $('#classFormError').textContent = '';
    $('#classDialog').showModal();
  }
  function payload() {
    const f = $('#classForm').elements, base = { courseId: f.courseId.value, title: f.title.value, kind: f.kind.value, group: f.group.value, time: f.time.value, endTime: f.endTime.value, location: f.location.value, cancelled: f.cancelled.checked, makeup: f.makeup.checked, exceptions: f.exceptions.value.split(/[,，\s]+/).filter(Boolean) };
    if (f.mode.value === 'single') base.dates = [f.date.value, ...f.dates.value.split(/[,，\s]+/)].filter(Boolean);
    else base.repeat = { start: f.start.value, end: f.end.value, weekdays: [...$('#classForm').querySelectorAll('[name="weekday"]:checked')].map(b => Number(b.value)), parity: f.parity.value, weeks: f.weeks.value.split(/[,，\s]+/).filter(Boolean).map(Number), basis: f.basis.value, anchor: f.anchor.value || null, semester: Number(f.semester.value) };
    return base;
  }
  async function change(action) {
    try {
      $('#classSave').disabled = true;
      const value = ['save', 'confirm'].includes(action) ? payload() : { courseId: $('#classForm').elements.courseId.value, group: $('#classForm').elements.group.value, kind: $('#classForm').elements.kind.value };
      await api('/api/timetable', { ...value, action, id: ui.edit?.id, digest: ui.edit?.digest });
      $('#classDialog').close(); await refresh(); showNotice('课程表已保存。');
    } catch (error) { $('#classFormError').textContent = error.message; }
    finally { $('#classSave').disabled = false; }
  }
  function init() {
    if (!$('#timetableBody') || ui.initialized) return;
    ui.initialized = true;
    $('[data-class-nav="prev"]').onclick = () => { ui.date = shift(ui.date, ui.view === 'week' ? -7 : -1); refresh(); };
    $('[data-class-nav="next"]').onclick = () => { ui.date = shift(ui.date, ui.view === 'week' ? 7 : 1); refresh(); };
    $('[data-class-nav="today"]').onclick = () => { ui.date = today(); ui.month = ui.date.slice(0, 7); ui.year = ''; refresh(); };
    document.querySelectorAll('[data-class-view]').forEach(b => b.onclick = () => { ui.view = b.dataset.classView; refresh(); });
    $('#timetableCourse').onchange = event => { ui.course = event.target.value; refresh(); };
    document.querySelectorAll('[data-calendar-nav]').forEach(b => b.onclick = () => { const d = new Date(ui.month + '-15T12:00:00+08:00'); d.setUTCMonth(d.getUTCMonth() + Number(b.dataset.calendarNav)); ui.month = d.toISOString().slice(0, 7); refresh(); });
    $('#calendarYear').onchange = event => { ui.year = event.target.value; ui.month = ui.year.slice(0, 4) + '-09'; refresh(); };
    $('#calendarDays').onclick = event => { const b = event.target.closest('[data-calendar-date]'); if (b) { ui.date = b.dataset.calendarDate; ui.calendarFocus = ui.date; ui.view = 'day'; refresh(); } };
    $('#calendarDays').onkeydown = event => {
      const button = event.target.closest('[data-calendar-date]');
      if (!button || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      // Accumulate rapid key presses while the previous calendar request is pending.
      const date = ui.calendarFocus || button.dataset.calendarDate;
      const weekday = (new Date(date + 'T12:00:00+08:00').getUTCDay() + 6) % 7;
      const offset = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -weekday, End: 6 - weekday }[event.key];
      ui.date = shift(date, offset); ui.month = ui.date.slice(0, 7); ui.calendarFocus = ui.date; ui.view = 'day'; refresh();
    };
    $('#calendarRefresh').onclick = async () => { try { await api('/api/calendar/refresh'); $('#calendarStatus').textContent = '正在检查官方修订…'; $('#calendarRefresh').disabled = true; state.calendarBusy = true; schedulePoll(); } catch (error) { showNotice(error.message, 'error'); } };
    $('#addClassButton').onclick = () => formRow();
    $('#timetablePanel').onclick = async event => {
      if (event.target.closest('[data-class-all-pending]')) { await refresh(true); return; }
      const b = event.target.closest('[data-class-detail]');
      if (!b) return;
      let row = [...(ui.timetable?.events || []), ...(ui.timetable?.pending || [])].find(r => r.id === b.dataset.classDetail);
      if (!row) return;
      try {
        if (row.summary) row = await read('/api/timetable?' + new URLSearchParams({ date: ui.date, view: ui.view, detail: row.id, ...(ui.course ? { course: ui.course } : {}) }), ui.token);
        formRow(row);
      } catch (error) { if (error.name !== 'AbortError') showNotice(error.message, 'error'); }
    };
    $('#classClose').onclick = () => $('#classDialog').close();
    $('#classForm').elements.mode.onchange = event => { $('#classRepeatFields').hidden = event.target.value !== 'repeat'; };
    $('#classForm').onsubmit = event => { event.preventDefault(); change(ui.edit && !ui.edit.manual ? 'confirm' : 'save'); };
    $('#classHide').onclick = () => change('hide'); $('#classDelete').onclick = () => change('delete'); $('#classGroup').onclick = () => change('group');
    $('.calendar-footer a').onclick = event => {
      if (window.pywebview?.api?.native_action) { event.preventDefault(); window.pywebview.api.native_action('academicCalendar').catch(error => showNotice(String(error),'error')); }
    };
    $('#softwareUpdateButton').onclick = () => nativeAction('softwareUpdate');
  }
  function render(data) {
    init(); ui.data = data; advanceDay();
    const key = JSON.stringify([data.today, data.courses, data.timetable, data.academicCalendar, data.events, data.overdue, data.connection?.authorized, data.featuresRevision]);
    if (key === ui.key) {
      $('#addClassButton').disabled = !data.connection?.authorized || !data.courses?.length || Boolean(data.connection?.busy);
      return;
    }
    ui.key = key;
    if (!ui.date) { ui.date = data.today; ui.month = ui.date.slice(0, 7); }
    if (data.academicCalendar && ui.month === data.academicCalendar.month && !ui.year) { ui.calendar = data.academicCalendar; renderCalendar(); }
    if (data.timetable && ui.date === data.today && ui.view === 'day' && !ui.course) { ui.timetable = data.timetable; renderTimetable(); }
    $('#timetableCourse').innerHTML = '<option value="">全部课程</option>' + (data.courses || []).map(c => '<option value="' + e(c.id) + '"' + (String(c.id) === ui.course ? ' selected' : '') + '>' + e(c.code || c.name) + '</option>').join('');
    if (!$('#classDialog').open) $('#classForm').elements.courseId.innerHTML = (data.courses || []).map(c => '<option value="' + e(c.id) + '">' + e(c.code || c.name) + '</option>').join('');
    if (!data.connection?.authorized) { if (ui.calendar) renderCalendar(); ui.timetable = { events: [], pending: [] }; if ($('#classDialog').open) $('#classDialog').close(); renderTimetable(); }
    if (!(data.academicCalendar && ui.month === data.academicCalendar.month && !ui.year && data.timetable && ui.date === data.today && ui.view === 'day' && !ui.course)) refresh();
  }
  function advanceDay() {
    const day = today(), previous = ui.currentDay;
    if (previous && previous !== day) {
      if (ui.date === previous) ui.date = day;
      if (!ui.year && ui.month === previous.slice(0, 7)) ui.month = day.slice(0, 7);
    }
    ui.currentDay = day;
  }
  window.academicUI = { init, render, refresh, cancel, tick: advanceDay };
})();