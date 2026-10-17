/* Supplemental browser regression. All account data is synthetic; external requests are blocked. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
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
  return { today: day, version: '2.0.0', revision: authorized ? 'synthetic-authorized' : 'synthetic-empty',
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
let data = fixture(false), failDocuments = false, failSave = false, requests = [], delayAcademic = 0;
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.ico': 'image/x-icon', '.jpg': 'image/jpeg' };
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://127.0.0.1'); requests.push(u.pathname);
  const send = (status, value) => {
    const finish = () => { if (!res.destroyed) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); } };
    if (delayAcademic && ['/api/calendar', '/api/timetable'].includes(u.pathname)) setTimeout(finish, delayAcademic);
    else finish();
  };
  if (u.pathname.startsWith('/api/')) {
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
  res.writeHead(200, { 'Content-Type': mime[path.extname(name)] || 'text/plain' });
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
    await page.addInitScript(() => { window.syntheticActions = []; window.webkit = { messageHandlers: { polyu: { postMessage: v => window.syntheticActions.push(v) } } }; });
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
    await check('no uncaught browser errors', async () => assert.deepEqual(errors, []));
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ ok: true, checks, errors, syntheticDataOnly: true, realCanvasValidated: false }, null, 2));
    console.log(JSON.stringify({ ok: true, checks: checks.length, output }));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ checks, errors, failure: String(error) }, null, 2)); process.exitCode = 1; }).finally(() => server.close());
