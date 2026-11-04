/* Real loopback backend + browser review regression. All account contents are synthetic. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const { createHash } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.build/review-ui');
fs.mkdirSync(output, { recursive: true });
for (const name of ['failure.json', 'failure.png']) fs.rmSync(path.join(output, name), { force: true });
const checks = [], errors = [];
let fixture, browser, page, fixtureErrors = '';
async function check(name, work) { await work(); checks.push(name); console.log('PASS ' + name); }
async function main() {
  fixture = spawn(process.env.PYTHON_EXECUTABLE || 'python3', ['-B', path.join(__dirname, 'review_fixture.py')], {
    cwd: root, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }
  });
  fixture.stderr.on('data', chunk => { fixtureErrors += chunk; });
  const lines = [], waiting = [];
  readline.createInterface({ input: fixture.stdout }).on('line', line => {
    try { const value = JSON.parse(line); if (waiting.length) waiting.shift()(value); else lines.push(value); }
    catch { fixtureErrors += line + '\n'; }
  });
  const next = () => Promise.race([lines.length ? Promise.resolve(lines.shift()) : new Promise(resolve => waiting.push(resolve)),
    new Promise((_, reject) => { const timer = setTimeout(() => reject(new Error('Fixture timed out: ' + fixtureErrors.slice(-4000))), 15000); timer.unref(); })]);
  const command = async name => { fixture.stdin.write(JSON.stringify({ command: name }) + '\n'); return next(); };
  const start = await next();
  browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  await context.route('**/*', route => new URL(route.request().url()).origin === start.base ? route.continue() : route.abort());
  page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(String(error)));
  await page.addInitScript(() => {
    window.cspViolations = []; document.addEventListener('securitypolicyviolation', e => window.cspViolations.push(e.violatedDirective));
    window.syntheticActions = []; window.webkit = { messageHandlers: { polyu: { postMessage: value => window.syntheticActions.push(value) } } };
  });
  const read = () => page.evaluate(async () => (await fetch('/api/data')).json());
  const refresh = async () => { await page.reload(); await page.waitForFunction(() => document.querySelector('#metricCourses').textContent === '1'); };
  const open = async id => { await page.locator(`[data-review-id="${id}"]`).first().click(); await page.locator('#reviewDialog').waitFor({ state: 'visible' }); };
  const save = async () => { await page.locator('#reviewSave').click(); await page.locator('#reviewDialog').waitFor({ state: 'hidden' }); };
  const reset = async id => {
    if (!await page.locator('#reviewHistory').evaluate(e => e.open)) await page.locator('#reviewHistory > summary').click();
    page.once('dialog', d => d.accept()); await page.locator(`[data-review-reset="${id}"]`).click();
    await page.waitForFunction(id => !document.querySelector(`[data-review-reset="${id}"]`), id);
  };
  await page.goto(start.base + '/#bootstrap=' + start.ticket);
  await page.waitForFunction(() => document.querySelector('#metricCourses').textContent === '1');
  const dateId = 'date:document:report-date';
  await check('real backend separates conflicts from unpublished dates and provides visible actions', async () => {
    assert.equal(await page.locator('#reviewList .review-row').count(), 4);
    assert.equal(await page.locator('#undatedCount').textContent(), '1');
    assert.match(await page.locator('#undatedLabel').textContent(), /尚未公布|未公布|待公布/);
    assert(await page.locator('#pendingShortcut').isVisible());
    await page.locator('#pendingShortcut').click();
    assert(await page.locator('#reviewCenter').isVisible());
    await page.screenshot({ path: path.join(output, 'review-center-desktop.png'), fullPage: true });
  });
  await check('failed review save preserves dialog and all input until retry succeeds', async () => {
    await open(dateId);
    await page.locator('#reviewForm [name="date"]').fill(start.date);
    await page.locator('#reviewForm [name="time"]').fill('17:00');
    await page.locator('#reviewNote').fill('Confirmed against synthetic outline');
    const fail = route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Synthetic save failure; retry.' }) });
    await page.route('**/api/evidence/review', fail);
    await page.locator('#reviewSave').click();
    await page.locator('#reviewFormError').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#reviewForm [name="date"]').inputValue(), start.date);
    assert.equal(await page.locator('#reviewNote').inputValue(), 'Confirmed against synthetic outline');
    assert(await page.locator('#reviewDialog').evaluate(e => e.open));
    await page.unroute('**/api/evidence/review', fail);
    await save();
    assert.equal(await page.locator(`#reviewList [data-review-id="${dateId}"]`).count(), 0);
    assert.match(await page.locator('#eventList').textContent(), /Research proposal/);
    const data = await read();
    assert(!data.undated.some(r => r.id === 'report-date'));
    const event = data.events.find(r => r.reviewId === dateId);
    assert(event); assert.equal(event.date, start.date); assert.equal(event.time, '17:00');
    const disk = await command('inspect');
    assert.equal(disk.reviewMode, '0o600');
    assert.equal(disk.reviews.decisions[dateId].action, 'confirm');
  });
  await check('confirmation survives reload and updates calendar and exported ICS', async () => {
    await refresh();
    assert.equal(await page.locator(`#reviewList [data-review-id="${dateId}"]`).count(), 0);
    assert.equal(await page.locator('#reviewHistory').evaluate(e => e.open), false);
    await page.evaluate(date => window.academicUI.select(date), start.date);
    await page.waitForFunction(() => document.querySelector('#selectedDateEvents').textContent.includes('Research proposal'));
    await page.locator('#exportCalendarButton').click();
    await page.waitForFunction(() => window.syntheticActions.some(a => a.action === 'exportsFolder'));
    const disk = await command('inspect'); assert.match(disk.ics, /Research proposal/);
    assert.match(disk.ics, new RegExp(start.date.replaceAll('-', '') + 'T090000Z'));
  });
  await check('undo restores pending state and removes the applied arrangement', async () => {
    await reset(dateId);
    assert.equal(await page.locator(`#reviewList [data-review-id="${dateId}"]`).count(), 1);
    assert.doesNotMatch(await page.locator('#eventList').textContent(), /Research proposal/);
    await open(dateId); await page.locator('#reviewForm [name="date"]').fill(start.date); await save();
  });
  await check('reviewed Canvas dates retain explicit unsubmitted evidence and completion confirmation', async () => {
    await page.locator('.undated-details > summary').click();
    await open('date:canvas:2'); await page.locator('#reviewForm [name="date"]').fill(start.date); await save();
    const data = await read(); const event = data.events.find(r => r.reviewId === 'date:canvas:2');
    assert.equal(event.submissionState, 'unsubmitted'); assert.equal(event.submissionStateObserved, true);
    const mark = page.locator('#eventList .event-row').filter({ hasText: 'Undated essay' }).locator('[data-action="mark"]');
    page.once('dialog', dialog => dialog.dismiss()); await mark.click();
    assert.equal((await read()).events.find(r => r.reviewId === 'date:canvas:2').done, false);
    page.once('dialog', dialog => dialog.accept()); await mark.click();
    await page.waitForFunction(() => !document.querySelector('#eventList').textContent.includes('Undated essay'));
    assert.equal((await read()).events.find(r => r.reviewId === 'date:canvas:2').done, true);
    await reset('date:canvas:2');
  });
  await check('changed original source invalidates the old confirmation and rejects stale submits', async () => {
    await command('source-change'); await refresh();
    assert.equal(await page.locator(`#reviewList [data-review-id="${dateId}"]`).count(), 1);
    assert.doesNotMatch(await page.locator('#eventList').textContent(), /Research proposal/);
    const data = await read(); assert.equal(data.reviews.items.find(r => r.id === dateId).stale, true);
    const old = data.reviews.history.find(r => r.id === dateId);
    const result = await page.evaluate(async old => {
      const response = await fetch('/api/evidence/review', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PolyU-Console': csrf }, body: JSON.stringify({ id: old.id, digest: old.digest, action: 'confirm', date: '2026-11-10' }) });
      return { status: response.status, body: await response.json() };
    }, old);
    assert.equal(result.status, 400); assert.match(result.body.error, /更新/);
  });
  await check('not applicable requires a reason and can be undone without creating an event', async () => {
    await open(dateId); await page.locator('#reviewDismiss').click();
    await page.locator('#reviewFormError').waitFor({ state: 'visible' });
    await page.locator('#reviewNote').fill('This synthetic requirement applies to another group');
    await page.locator('#reviewDismiss').click(); await page.locator('#reviewDialog').waitFor({ state: 'hidden' });
    assert.doesNotMatch(await page.locator('#eventList').textContent(), /Research proposal/);
    assert.equal(await page.locator(`#reviewList [data-review-id="${dateId}"]`).count(), 0);
    await reset(dateId);
  });
  await check('time and location reviews enter the corresponding exam and disappear from pending', async () => {
    for (const [field, values] of [['time', { time: '14:00', endTime: '16:00' }], ['location', { location: 'B301' }]]) {
      const item = (await read()).reviews.items.find(r => r.type === 'detail' && r.field === field);
      await open(item.id);
      for (const [name, value] of Object.entries(values)) await page.locator(`#reviewForm [name="${name}"]`).fill(value);
      await save();
      const data = await read(); assert(!data.reviews.items.some(r => r.id === item.id));
      const exam = data.events.find(r => r.title === 'Final exam');
      for (const [name, value] of Object.entries(values)) assert.equal(exam[name], value);
    }
    assert.match(await page.locator('#eventList').textContent(), /B301/);
  });
  await check('full grading confirmation enforces 100 percent and applies only the published grade', async () => {
    const item = (await read()).reviews.items.find(r => r.type === 'grading');
    await open(item.id);
    await page.locator('[data-review-weight="report"]').fill('50');
    await page.locator('#reviewSave').click(); await page.locator('#reviewFormError').waitFor({ state: 'visible' });
    assert.match(await page.locator('#reviewTotal').textContent(), /110/);
    await page.locator('[data-review-weight="report"]').fill('40'); await save();
    let data = await read(); const grade = data.grades.courses[0];
    assert.equal(grade.earned, 32); assert.equal(grade.assessedWeight, 40);
    assert.equal(grade.components.find(r => r.name === 'Final exam').weighted, null);
    assert(!data.reviews.items.some(r => r.type === 'grading'));
    await reset(item.id); data = await read(); assert.equal(data.grades.courses[0].earned, null);
  });
  await check('review forms remain usable at 390 pixels with keyboard close and no horizontal overflow', async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await open(dateId);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert(await page.locator('#reviewDialog').evaluate(e => e.scrollWidth <= e.clientWidth + 1));
    assert(await page.locator('#reviewClose').evaluate(e => {
      const rect = e.getBoundingClientRect(), dialog = document.querySelector('#reviewDialog').getBoundingClientRect();
      return rect.top >= dialog.top && rect.bottom <= innerHeight;
    }), 'The initial mobile dialog must expose its close button');
    await page.screenshot({ path: path.join(output, 'review-dialog-mobile.png') });
    await page.keyboard.press('Escape'); await page.locator('#reviewDialog').waitFor({ state: 'hidden' });
    assert(await page.evaluate(() => document.activeElement.hasAttribute('data-review-id')));
  });
  await check('logout removes the review ledger and clears account data from the rendered UI', async () => {
    await open(dateId); await page.locator('#reviewForm [name="date"]').fill(start.date); await save();
    const result = await command('logout'); assert.equal(result.remainingFiles, 0);
    await page.goto(start.base + '/?synthetic-logout=1#bootstrap=' + result.ticket);
    await page.waitForFunction(() => document.querySelector('#metricCourses').textContent === '0');
    assert.equal(await page.locator('#reviewList .review-row').count(), 0);
    assert.equal(await page.locator('#reviewHistoryList .review-row').count(), 0);
    assert.doesNotMatch(await page.locator('#eventList').textContent(), /Research proposal|Final exam/);
    const disk = await command('inspect'); assert.deepEqual(disk.reviews, {}); assert.equal(disk.ics, '');
  });
  await check('real CSP and browser execution remain clean', async () => {
    assert.deepEqual(errors, []); assert.deepEqual(await page.evaluate(() => window.cspViolations), []);
  });
  const sourceHashes = Object.fromEntries(['学习控制台/app.js', '学习控制台/index.html', '学习控制台/styles.css', '学习控制台/server.py', '学习控制台/evidence_review.py', '学习控制台/grades.py', '验证/review_fixture.py', '验证/test_review_ui.cjs'].map(name => [name, createHash('sha256').update(fs.readFileSync(path.join(root, name))).digest('hex')]));
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ ok: true, checks, errors, sourceHashes, syntheticDataOnly: true, realBackend: true, realCanvasValidated: false }, null, 2));
  await command('stop');
  console.log(JSON.stringify({ ok: true, checks: checks.length, output }));
}
main().catch(async error => {
  console.error(error);
  if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
  fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ checks, errors, error: String(error), backend: fixtureErrors.slice(-10000) }, null, 2));
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (fixture && fixture.exitCode === null) {
    fixture.stdin.end();
    await new Promise(resolve => {
      const timer = setTimeout(() => { fixture.kill(); resolve(); }, 5000);
      fixture.once('exit', () => { clearTimeout(timer); resolve(); });
    });
  }
});
