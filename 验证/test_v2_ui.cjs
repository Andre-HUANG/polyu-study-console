/* Supplemental browser regression. All account data is synthetic; external requests are blocked. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const release = JSON.parse(fs.readFileSync(path.join(root, '学习控制台/release.json'), 'utf8'));
const output = path.join(root, '.build/v2-ui');
fs.mkdirSync(output, { recursive: true });
const day = '2026-10-18';
const source = 'https://canvas.polyu.edu.hk/courses/90001';
const courses = [
  { id: '90001', code: 'DEMO201', name: 'Design for Sustainable Cities', term: 'Synthetic 2026/27' },
  { id: '90002', code: 'DEMO302', name: 'Materials and the Environment', term: 'Synthetic 2026/27' },
  { id: '90003', code: 'DEMO403', name: 'Research Methods', term: 'Synthetic 2026/27' },
];
const event = (id, title, date, daysLeft, kind = 'assignment', course = 'DEMO201') => ({
  id, title, rawTitle: title, date, daysLeft, kind, course, courseName: courses.find(c => c.code === course).name,
  ...(kind === 'assignment' ? { markKey: id.replace(/[^0-9]/g, '').padStart(32, 'a'), done: false } : {}),
  time: kind === 'assignment' ? '23:59' : '14:30', location: kind === 'assignment' ? '' : '教学楼 A201',
  weight: '—', source: 'canvas', sourceLabel: 'Canvas 作业', status: '未交', editable: false, url: source,
  sources: [{ title: 'Synthetic course outline.pdf', locator: '第 3 页', quote: 'Synthetic evidence only.', url: source }],
});
const docs = { complete: true, completedAt: '2026-10-18T02:00:00Z', fileCount: 8, readFiles: 8,
  pageCount: 64, processedPages: 64, findingCount: 5, reviewCount: 1, courses: [], evidence: {},
  records: [{ id: 'synthetic-file', course: 'DEMO201', title: 'Synthetic course outline.pdf', type: 'file',
    status: 'read', findings: [], methods: ['text'], pageCount: 8, processedPages: 8, url: source }] };
const calendar = { month: '2026-10', year: '2026/27', years: ['2026/27'], events: [],
  teachingWeek: { semester: 1, number: 7 }, checkedAt: '2026-10-18T01:00:00Z' };
const timetable = { date: day, view: 'day', days: [day], pending: [], issues: [], events: [
  { id: 'synthetic-class', courseId: '90001', course: 'DEMO201', title: 'Design studio', date: day,
    time: '10:00', endTime: '12:00', kind: 'lecture', location: '教学楼 A201', sources: [], manual: true },
] };
function fixture(authorized = true) {
  const events = authorized ? [event('canvas:1', 'Urban systems report', day, 0),
    event('canvas:2', 'Midterm assessment', '2026-10-21', 3, 'midterm', 'DEMO302'),
    event('canvas:3', 'Research proposal', '2026-11-05', 18, 'assignment', 'DEMO403')] : [];
  return { today: day, version: release.version, revision: authorized ? 'synthetic-authorized' : 'synthetic-empty',
    courseCount: authorized ? courses.length : 0, courses: authorized ? courses : [], events,
    overdue: authorized ? [{ ...event('canvas:4', 'Lab reflection', '2026-10-16', -2), overdue: true,
      submissionConfirmed: true, overdueLabel: 'Canvas 显示未提交', overdueReason: 'Canvas 显示未提交' }] : [],
    undated: authorized ? [{ id: 'pending:1', course: 'DEMO403', title: 'Final assessment', kind: 'final',
      reason: '文件中的日期存在冲突，请核对原文。', url: source, sources: [] }] : [], recent: [],
    lastSync: authorized ? '2026-10-18T02:00:00Z' : null, snapshotComplete: true,
    freshness: { state: 'fresh', warnings: [], lastCompleteAt: '2026-10-18T02:00:00Z' },
    recentChanges: [], documents: authorized ? structuredClone(docs) : {},
    connection: { native: true, authorized, busy: false, needsLogin: false, autoSync: false,
      name: authorized ? 'Synthetic Student' : '', syncIntervalSeconds: 3600 },
    grades: { recent: [], courses: authorized ? courses.map((c, i) => ({ ...c, components: [], otherGrades: [],
      earned: null, canvasCurrentPercent: i === 0 ? 86 : null, basis: 'canvas', gradedCount: i === 0 ? 2 : 0 })) : [] },
    academicCalendar: calendar, timetable: authorized ? timetable : { ...timetable, events: [] } };
}
let data = fixture(false), failDocuments = false, failSave = false, requests = [], delayAcademic = 0, posts = [];
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.ico': 'image/x-icon', '.jpg': 'image/jpeg' };
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://127.0.0.1'); requests.push(u.pathname);
  const send = (status, value) => {
    const finish = () => { if (!res.destroyed) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); } };
    if (delayAcademic && ['/api/calendar', '/api/timetable'].includes(u.pathname)) setTimeout(finish, delayAcademic);
    else finish();
  };
  if (u.pathname.startsWith('/api/')) {
    if (req.method === 'POST' && ['/api/event/mark', '/api/export/calendar'].includes(u.pathname)) {
      let body = ''; req.on('data', chunk => { body += chunk; });
      return req.on('end', () => {
        const payload = JSON.parse(body || '{}'); posts.push({ path: u.pathname, payload });
        if (u.pathname === '/api/export/calendar') return send(200, { message: `已导出 ${payload.ids.length} 项安排到本机日历文件 polyu-schedule.ics`, data });
        const row = [...data.events, ...data.overdue].find(r => r.markKey === payload.key);
        if (!row) return send(400, { error: '合成测试：标记不存在' });
        row.done = payload.done; if (payload.done) row.doneAt = '2026-10-18T10:00:00+08:00'; else delete row.doneAt;
        data = { ...data, revision: data.revision + '+' };
        return send(200, { message: payload.done ? '已标记完成。' : '已恢复为待完成。', data });
      });
    }
    if (u.pathname === '/api/data') return send(200, data);
    if (u.pathname === '/api/status') return send(200, { revision: data.revision, connection: data.connection });
    if (u.pathname === '/api/documents') return send(failDocuments ? 503 : 200, failDocuments ? { error: '合成测试：文档暂不可读' } : docs);
    if (u.pathname === '/api/documents/search') return send(200, { results: [{ course: 'DEMO201', locator: '第 3 页',
      title: 'Synthetic course outline.pdf', excerpt: 'Synthetic deadline evidence for report.', readAt: '2026-10-18T02:00:00Z', url: source }] });
    if (u.pathname === '/api/calendar') return send(200, { ...calendar, month: u.searchParams.get('month') || calendar.month });
    if (u.pathname === '/api/timetable') return send(200, { ...timetable, date: u.searchParams.get('date') || day,
      events: data.connection.authorized ? timetable.events.filter(e => e.date === (u.searchParams.get('date') || day)) : [] });
    if (req.method === 'POST' && failSave) return send(503, { error: '合成测试：保存未完成，请重试。' });
    return send(200, { data });
  }
  const name = u.pathname === '/' ? 'index.html' : u.pathname.slice(1);
  const allowed = ['index.html', 'app.js', 'academic-ui.js', 'styles.css', 'assets/polyu-logo.png', 'assets/polyu-favicon.ico', 'assets/polyu-emblem.jpg'];
  if (!allowed.includes(name)) { res.writeHead(404); return res.end(); }
  // Mirror the production policy so inline styles or scripts fail this regression.
  res.writeHead(200, { 'Content-Type': mime[path.extname(name)] || 'text/plain', 'Content-Security-Policy': "default-src 'self' data:; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" });
  res.end(fs.readFileSync(path.join(root, '学习控制台', name)));
});
const checks = [], errors = [];
async function check(name, work) { await work(); checks.push(name); console.log('PASS ' + name); }
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    const page = await context.newPage(); page.on('pageerror', e => errors.push(String(e)));
    await page.addInitScript(() => { window.cspViolations = []; document.addEventListener('securitypolicyviolation', e => window.cspViolations.push(e.violatedDirective + ' ' + (e.sample || ''))); window.syntheticActions = []; window.webkit = { messageHandlers: { polyu: { postMessage: v => window.syntheticActions.push(v) } } }; });
    await page.goto(base); await page.waitForFunction(() => document.querySelector('#metricCourses').textContent === '0');
    await check('first connection is actionable', async () => {
      await page.locator('#welcomeAction').click();
      assert(await page.evaluate(() => window.syntheticActions.some(a => a.action === 'login')) || await page.locator('#settingsDialog').evaluate(d => d.open));
      await page.evaluate(() => document.querySelector('#settingsDialog').close());
      await page.screenshot({ path: path.join(output, 'empty-1440.png'), fullPage: true });
    });
    data = fixture(true); await page.evaluate(value => { localRevision = value.revision; render(value); }, data);
    await check('populated dashboard renders source data', async () => {
      assert.equal(await page.locator('#metricCourses').textContent(), '3');
      assert.equal(await page.locator('#eventList .event-row').count(), 3);
      assert.equal(await page.locator('#undatedCount').textContent(), '1');
    });
    await check('same-revision connection changes update home actions', async () => {
      data.connection.busy = true; data.connection.stage = '合成测试：正在同步课程';
      await page.evaluate(() => poll());
      assert.equal(await page.locator('#workspaceWelcome').getAttribute('data-tone'), 'working');
      data.connection.busy = false; data.connection.needsLogin = true;
      await page.evaluate(() => poll());
      assert.equal(await page.locator('#welcomeAction').getAttribute('data-action'), 'login');
      data.connection.needsLogin = false; data.connection.stage = '';
      await page.evaluate(() => poll());
      assert.notEqual(await page.locator('#workspaceWelcome').getAttribute('data-tone'), 'working');
    });
    await check('release identity and urgency cues retain readable text', async () => {
      assert.equal(await page.locator('#appVersion').textContent(), 'v' + release.version);
      assert(await page.locator('#eventList .event-row').nth(0).evaluate(e => e.classList.contains('urgency-urgent')));
      assert(await page.locator('#eventList .event-row').nth(1).evaluate(e => e.classList.contains('urgency-soon')));
      assert(await page.locator('#eventList .event-row').nth(2).evaluate(e => e.classList.contains('urgency-calm')));
      assert.match(await page.locator('#eventList').textContent(), /Urban systems report|Midterm assessment/);
      assert.doesNotMatch(await page.locator('#eventList').textContent(), /週/);
    });
    await check('grade progress represents known scores and leaves unknown ones empty', async () => {
      const bars = page.locator('#gradeCourseGrid .grade-progress');
      assert.equal(await bars.count(), 3);
      assert.equal(await bars.nth(0).getAttribute('value'), '86');
      assert(await bars.nth(1).evaluate(e => e.classList.contains('is-empty')));
      assert(await bars.nth(2).evaluate(e => e.classList.contains('is-empty')));
      const changed = structuredClone(data); changed.grades.courses[0].canvasCurrentPercent = 125;
      await page.evaluate(value => render(value), changed);
      assert.equal(await page.locator('#gradeCourseGrid .grade-progress').nth(0).getAttribute('value'), '100');
      await page.evaluate(value => render(value), data);
    });
    await check('personal edit sends new course and title with original identity', async () => {
      const editable = { ...event('manual:synthetic', 'Original personal final', day, 0, 'final'),
        editable: true, source: 'manual', sourceLabel: '个人安排' };
      const changed = structuredClone(data); changed.events.push(editable);
      await page.evaluate(value => render(value), changed);
      await page.locator('[data-action="edit"][data-id="manual:synthetic"]').click();
      assert(await page.locator('#eventForm [name="course"]').isEnabled());
      assert.equal(await page.locator('#eventForm [name="title"]').getAttribute('readonly'), null);
      await page.locator('#eventForm [name="course"]').selectOption('DEMO302');
      await page.locator('#eventForm [name="title"]').fill('Renamed personal final');
      const request = page.waitForRequest(req => req.url().endsWith('/api/event/save'));
      await page.locator('#eventForm button[type="submit"]').click();
      const payload = (await request).postDataJSON();
      assert.equal(payload.course, 'DEMO302');
      assert.equal(payload.title, 'Renamed personal final');
      assert.equal(payload.kind, 'final');
      assert.deepEqual(payload.original, { course: 'DEMO201', title: 'Original personal final' });
      await page.locator('#eventDialog').waitFor({ state: 'hidden' });
      await page.evaluate(value => render(value), data);
    });
    await check('2.2 completion marks hide work, update counts and can be undone', async () => {
      assert.equal(await page.locator('#brandVersion').textContent(), release.version.split('.').slice(0, 2).join('.'));
      assert.equal(await page.locator('#metricWeek').textContent(), '2');
      const mark = page.locator('#eventList [data-action="mark"]').first();
      assert.equal(await mark.getAttribute('aria-pressed'), 'false');
      await mark.click();
      await page.waitForFunction(() => document.querySelectorAll('#eventList .event-row').length === 2);
      const sent = posts.at(-1); assert.equal(sent.path, '/api/event/mark'); assert.equal(sent.payload.done, true); assert.equal(sent.payload.confirm, undefined);
      assert.equal(await page.locator('#metricWeek').textContent(), '1');
      assert.match(await page.locator('#metricWeekRange').textContent(), /1 项已完成/);
      assert.equal(await page.locator('#focusTitle').textContent(), 'Midterm assessment');
      assert(await page.locator('#toggleDone').isVisible()); assert.equal(await page.locator('#doneCount').textContent(), '1');
      assert.match(await page.locator('#eventResults').textContent(), /1 项已完成已隐藏/);
      assert(await page.evaluate(() => document.activeElement.closest('#eventList') !== null));
      await page.locator('#toggleDone').click();
      assert.equal(await page.locator('#eventList .event-row').count(), 3);
      assert(await page.locator('#eventList .event-row.done-event').count() === 1);
      assert.match(await page.locator('#eventList .done-event').textContent(), /已标记完成/);
      await page.locator('#eventList .done-event [data-action="mark"]').click();
      await page.waitForFunction(() => !document.querySelector('#eventList .done-event'));
      assert.equal(posts.at(-1).payload.done, false);
      assert.equal(await page.locator('#metricWeek').textContent(), '2');
      await page.locator('#clearEventFilters').click();
      assert(await page.locator('#toggleDone').isHidden());
    });
    await check('2.2 explicit Canvas unsubmitted state asks before marking', async () => {
      const original = data;
      data = structuredClone(data); data.events[0] = { ...data.events[0], source: 'canvas', submissionState: 'unsubmitted', submissionStateObserved: true };
      data.revision += ':unsubmitted'; await page.evaluate(value => render(value), data);
      const count = posts.length;
      page.once('dialog', dialog => dialog.dismiss());
      await page.locator('#eventList [data-action="mark"]').first().click();
      await page.waitForTimeout(150); assert.equal(posts.length, count);
      page.once('dialog', dialog => dialog.accept());
      await page.locator('#eventList [data-action="mark"]').first().click();
      await page.waitForFunction(n => window.fetch && document.querySelectorAll('#eventList .event-row').length === 2, count);
      assert.equal(posts.at(-1).payload.confirm, true);
      data = original; await page.evaluate(value => render(value), data);
    });
    await check('2.2 calendar export sends the visible confirmed rows and reveals the file', async () => {
      await page.locator('#courseFilter').selectOption('DEMO302');
      await page.evaluate(() => { window.syntheticActions.length = 0; });
      await page.locator('#exportCalendarButton').click();
      await page.waitForFunction(() => window.syntheticActions.some(a => a.action === 'exportsFolder'));
      assert.deepEqual(posts.at(-1).payload.ids, ['canvas:2']);
      assert.match(await page.locator('#notice').textContent(), /已导出 1 项/);
      await page.locator('#clearEventFilters').click();
      await page.locator('#eventRange').selectOption('overdue');
      assert(await page.locator('#exportCalendarButton').isDisabled());
      await page.locator('#clearEventFilters').click();
      assert(await page.locator('#exportCalendarButton').isEnabled());
    });
    await check('2.2 workload strip covers 14 days and opens the calendar date', async () => {
      assert.equal(await page.locator('#workloadDays .workload-day').count(), 14);
      assert.match(await page.locator('#workloadSummary').textContent(), /未来 14 天待完成 2 项.*每天最多 1 项/);
      const heights = await page.locator('#workloadDays .workload-bar i').evaluateAll(bars => bars.map(b => b.style.height));
      assert(heights.length >= 2 && heights.every(h => /%$/.test(h)));
      await page.locator('[data-workload-date="2026-10-21"]').click();
      await page.waitForFunction(() => document.querySelector('[data-calendar-date="2026-10-21"]')?.getAttribute('aria-pressed') === 'true');
      assert.match(await page.locator('#selectedDateEvents').textContent(), /Midterm assessment/);
      await page.locator(`[data-calendar-date="${day}"]`).click();
    });
    await check('2.2 grade target planner uses only the verified remaining weight', async () => {
      const original = data;
      data = structuredClone(data); data.revision += ':planning';
      Object.assign(data.grades.courses[2], { earned: 18, assessedWeight: 20, currentPercent: 90, gradedCount: 1,
        planning: { totalWeight: 100, earned: 18, assessedWeight: 20, remainingWeight: 80 } });
      await page.evaluate(value => render(value), data);
      const card = page.locator('#gradeCourseGrid .grade-course-card').filter({ hasText: 'DEMO403' });
      await card.locator('summary').click();
      assert.equal(await page.locator('#gradeCourseGrid .grade-target').count(), 1);
      const input = card.locator('.grade-target input');
      await input.fill('80'); assert.match(await card.locator('.grade-target-result').textContent(), /77\.5%/);
      await input.fill('100'); assert.match(await card.locator('.grade-target-result').textContent(), /超过满分/);
      await input.fill('15'); assert.match(await card.locator('.grade-target-result').textContent(), /已不低于/);
      await input.fill('150'); assert.match(await card.locator('.grade-target-result').textContent(), /0–100/);
      await input.fill('80');
      await page.evaluate(value => render({ ...value, revision: value.revision + ':rerender', grades: structuredClone(value.grades) }), data);
      assert.equal(await card.locator('.grade-target input').inputValue(), '80');
      assert.match(await card.locator('.grade-target-result').textContent(), /77\.5%/);
      await card.locator('summary').click();
      data = original; await page.evaluate(value => render(value), data);
    });
    await check('2.2 sidebar follows the scrolled section', async () => {
      await page.evaluate(() => document.querySelector('#grades').scrollIntoView({ block: 'start' }));
      await page.waitForFunction(() => document.querySelector('.side-nav a[href="#grades"]').getAttribute('aria-current') === 'page');
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForFunction(() => document.querySelector('.side-nav a[href="#top"]').getAttribute('aria-current') === 'page');
    });
    await check('deadline search, ranges and clearing combine correctly', async () => {
      await page.locator('#eventRange').selectOption('7'); assert.equal(await page.locator('#eventList .event-row').count(), 2);
      await page.locator('#eventSearch').fill('research'); assert.equal(await page.locator('#eventList .event-row').count(), 0);
      await page.locator('#eventRange').selectOption('30'); assert.equal(await page.locator('#eventList .event-row').count(), 1);
      await page.locator('#clearEventFilters').click(); assert.equal(await page.locator('#eventList .event-row').count(), 3);
      await page.locator('#eventRange').selectOption('overdue'); assert.equal(await page.locator('#eventList .event-row').count(), 1);
      assert.match(await page.locator('#eventList').textContent(), /Lab reflection/);
      await page.locator('#clearEventFilters').click();
    });
    await check('deadline tabs support arrow keys', async () => {
      await page.locator('[data-filter="all"]').focus(); await page.keyboard.press('ArrowRight');
      assert.equal(await page.evaluate(() => document.activeElement.dataset.filter), 'midterm');
      await page.locator('#clearEventFilters').click();
    });
    await check('quick search opens and finds cached evidence', async () => {
      await page.keyboard.press('Meta+k'); await page.locator('#documentSearch').waitFor({ state: 'visible' });
      assert.equal(await page.evaluate(() => document.activeElement.id), 'documentSearch');
      await page.locator('#documentSearch').fill('deadline'); await page.waitForFunction(() => document.querySelector('#documentMatches').textContent.includes('Synthetic deadline evidence'));
      await page.locator('#closeSettingsButton').click();
    });
    await check('document errors are visible and retryable', async () => {
      failDocuments = true; await page.evaluate(() => { documentsRevision = null; openSettings('documents'); });
      await page.locator('#documentLoadError').waitFor({ state: 'visible' });
      assert.match(await page.locator('#documentLoadMessage').textContent(), /资料|文档/);
      failDocuments = false; await page.locator('#documentRetry').click();
      await page.locator('#documentLoadError').waitFor({ state: 'hidden' }); await page.locator('#closeSettingsButton').click();
    });
    await check('save errors stay inside the open event dialog', async () => {
      failSave = true; await page.locator('#addEventButton').click();
      await page.locator('#eventForm [name="course"]').selectOption('DEMO201');
      await page.locator('#eventForm [name="title"]').fill('Synthetic manual task');
      await page.locator('#eventForm [name="date"]').fill('2026-10-20');
      await page.locator('#eventForm [name="url"]').fill(source);
      await page.locator('#eventForm button[type="submit"]').click();
      await page.locator('#eventFormError').waitFor({ state: 'visible' });
      assert.match(await page.locator('#eventFormError').textContent(), /保存未完成/);
      assert(await page.locator('#eventDialog').evaluate(d => d.open));
      await page.locator('#cancelDialog').click(); failSave = false;
      assert.doesNotMatch(await page.locator('#notice').textContent(), /正在保存/);
    });
    await check('calendar exposes selected and current dates', async () => {
      const date = page.locator(`[data-calendar-date="${day}"]`);
      assert.equal(await date.getAttribute('aria-current'), 'date');
      await date.click(); await page.waitForFunction(() => document.querySelector('[data-calendar-date="2026-10-18"]').getAttribute('aria-pressed') === 'true');
      assert.match(await date.getAttribute('aria-label'), /安排|作业|考试/);
    });
    await check('rapid calendar arrows retain each step and focus', async () => {
      delayAcademic = 150;
      await page.locator(`[data-calendar-date="${day}"]`).focus();
      await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.activeElement.dataset.calendarDate === '2026-10-20');
      await page.locator('[data-calendar-date="2026-10-31"]').focus();
      await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.activeElement.dataset.calendarDate === '2026-11-02');
      await page.locator('[data-calendar-nav="-1"]').click();
      await page.locator(`[data-calendar-date="${day}"]`).click();
      delayAcademic = 0;
    });
    await page.evaluate(() => window.scrollTo(0, 0));
    for (const width of [1440, 1024, 768, 390]) await check(`layout fits ${width}px`, async () => {
      await page.setViewportSize({ width, height: 1000 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Horizontal page overflow at ${width}`);
      await page.screenshot({ path: path.join(output, `dashboard-${width}.png`), fullPage: true });
      await page.screenshot({ path: path.join(output, `viewport-${width}.png`) });
    });
    await check('hidden runtime does not poll', async () => {
      await page.evaluate(() => { state.data.connection.busy = true; window.polyuRuntimeState({ hidden: true, lowPower: false }); });
      await page.waitForTimeout(250); const count = requests.length; await page.waitForTimeout(2300); assert.equal(requests.length, count);
      await page.evaluate(() => { state.data.connection.busy = false; window.polyuRuntimeState({ hidden: false, lowPower: false }); });
    });
    await check('logout clears previous personal calendar and results', async () => {
      data = fixture(false); await page.evaluate(value => render(value), data);
      assert.equal(await page.locator('#eventList .event-row').count(), 0);
      assert.equal(await page.locator('.calendar-days .has-arrangements').count(), 0);
      assert.doesNotMatch(await page.locator('#selectedDateEvents').textContent(), /Urban|Synthetic|DEMO/);
      assert.equal(await page.locator('#gradeCourseGrid .grade-course-card').count(), 0);
    });
    await check('no uncaught browser errors or CSP violations', async () => {
      assert.deepEqual(errors, []);
      assert.deepEqual(await page.evaluate(() => window.cspViolations), []);
    });
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ ok: true, checks, errors, syntheticDataOnly: true, realCanvasValidated: false }, null, 2));
    console.log(JSON.stringify({ ok: true, checks: checks.length, output }));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ checks, errors, failure: String(error) }, null, 2)); process.exitCode = 1; }).finally(() => server.close());
