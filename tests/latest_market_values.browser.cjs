/* 확정·잠정 금리를 함께 표시하는 방향성 화면의 브라우저 회귀검증입니다. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'tmp/latest-rates-qa');

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const server = http.createServer((req, res) => {
    const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    const file = path.resolve(root, '.' + (relative === '/' ? '/index.html' : relative));
    if (!file.startsWith(root + path.sep)) {res.writeHead(403).end(); return;}
    try {
      const types = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.json':'application/json'};
      res.setHeader('Content-Type', (types[path.extname(file)] || 'application/octet-stream') + '; charset=utf-8');
      res.end(fs.readFileSync(file));
    } catch {res.writeHead(404).end();}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:process.env.MARKET_QA_BROWSER || undefined});
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // 운영 원장을 수정하지 않고, 같은 거래일의 후속 시세를 응답에 주입합니다.
    await page.route('**/naver-marketindex-history.json*', route => {
      const data = JSON.parse(fs.readFileSync(path.join(root, 'data/naver-marketindex-history.json')));
      data.liveSnapshotsGeneratedAt = '2026-11-03 09:00:00 KST';
      data.series.us10y_naver = data.series.us10y_naver.filter(row => row.date < '2026-11-02');
      data.series.us10y_naver.push({date:'2026-11-02', close:4.894, observedAt:'2026-11-02T17:05:00-05:00'});
      data.liveSnapshots.us10y_naver = {date:'2026-11-02', close:4.915, observedAt:'2026-11-02T18:30:00-05:00', isProvisional:true, fetchStatus:'live'};
      return route.fulfill({json:data});
    });
    for (const width of [1366, 390]) {
      await page.setViewportSize({width, height:900});
      await page.goto(`http://127.0.0.1:${server.address().port}/#market`);
      const row = page.locator('.market-trend-row').filter({has:page.locator('.market-trend-row__identity strong', {hasText:/^미국 10년$/})});
      await row.waitFor({state:'visible', timeout:30000});
      assert.match(await row.innerText(), /최신 2026-11-03 08:30 KST · 잠정/);
      assert.match(await row.innerText(), /확정 2026-11-02 · 4.894%/);
      assert.equal(await row.locator('.market-trend-row__current dd').innerText(), '4.915%');
      await row.scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(output, `rates-${width}.png`)});
      const bounds = await row.evaluate(el => ({width:el.clientWidth, scroll:el.scrollWidth}));
      assert(bounds.scroll <= bounds.width + 1, '금리 행의 글씨가 가로로 넘칩니다.');
      const chart = row.locator('svg:visible');
      const box = await chart.boundingBox();
      await page.mouse.move(box.x + box.width - 0.1, box.y + box.height / 2);
      const tooltip = row.locator('.chart-cursor-tooltip.is-visible');
      assert.match(await tooltip.innerText(), /4.915%/);
      assert.match(await tooltip.innerText(), /2026-11-03 08:30 KST/);
      await page.screenshot({path:path.join(output, `hover-${width}.png`)});
    }
    if (process.env.MARKET_QA_OFFLINE) {
      const offline = await browser.newContext({offline:true, viewport:{width:1366, height:900}});
      const tab = await offline.newPage();
      tab.on('pageerror', e => errors.push(e.message));
      await tab.goto(pathToFileURL(path.resolve(process.env.MARKET_QA_OFFLINE)).href + '#market');
      const row = tab.locator('.market-trend-row').filter({has:tab.locator('.market-trend-row__identity strong', {hasText:/^미국 10년$/})});
      await row.waitFor({state:'visible'});
      assert.match(await row.innerText(), /최신.*KST · 잠정/);
      assert.match(await row.innerText(), /확정/);
      await row.scrollIntoViewIfNeeded();
      await tab.screenshot({path:path.join(output, 'offline.png')});
      await offline.close();
    }
    assert.deepEqual(errors, []);
    console.log('최신값·확정값 병기 / 한국시간 / 데스크톱·모바일 표시 검증 통과');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
