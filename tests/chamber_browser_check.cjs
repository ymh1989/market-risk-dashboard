/* 독립 HTML을 실제 브라우저에서 열어 픽셀·입력·오프라인 동작을 검증합니다. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');
const { PNG } = require('pngjs');
const root = path.resolve(__dirname, '..');
const output = process.env.CHAMBER_QA_OUTPUT || path.join(root, 'tmp/chamber-qa');
fs.mkdirSync(output, { recursive:true });

function pixels(buffer) {
  const png = PNG.sync.read(buffer);
  let lit=0,bright=0,total=0;
  // UI를 제외한 중앙부의 실제 3D 픽셀만 측정합니다.
  for(let y=Math.floor(png.height*.15);y<png.height*.72;y++)for(let x=Math.floor(png.width*.16);x<png.width*.84;x++){
    const i=(y*png.width+x)*4,max=Math.max(...png.data.subarray(i,i+3));
    if(max>45)lit++;if(max>120)bright++;total++;
  }
  return {lit:lit/total,bright:bright/total};
}

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHAMBER_BROWSER || undefined,args:['--enable-webgl','--ignore-gpu-blocklist']});
  const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1,acceptDownloads:true});
  const page=await context.newPage(),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  const result={};
  try {
    await page.goto(pathToFileURL(path.join(root,'chamber-lab.html')).href);
    await page.waitForFunction(()=>document.body.dataset.ready==='true',{timeout:30000});
    await page.waitForFunction(()=>Number(document.querySelector('canvas').dataset.frame)>35);
    let before=await page.locator('canvas').screenshot();result.desktop=pixels(before);
    fs.writeFileSync(path.join(output,'desktop.png'),await page.screenshot());
    assert(result.desktop.lit>.08,'3D 장면 픽셀이 너무 적습니다.');
    let frame=await page.locator('canvas').getAttribute('data-frame');
    await page.waitForFunction(f=>Number(document.querySelector('canvas').dataset.frame)>Number(f)+20,frame);
    assert(!before.equals(await page.locator('canvas').screenshot()),'애니메이션이 정지되어 있습니다.');
    await page.getByRole('button',{name:'일시정지',exact:true}).click();
    await page.waitForTimeout(150);
    frame=await page.locator('canvas').getAttribute('data-frame');
    await page.waitForTimeout(200);
    assert.equal(await page.locator('canvas').getAttribute('data-frame'),frame,'일시정지가 렌더링을 멈추지 않았습니다.');
    for(let n=0;n<3;n++){
      await page.getByRole('button',{name:`형태 ${n+1}`,exact:true}).click();
      await page.waitForFunction(f=>document.querySelector('canvas').dataset.form===String(f),n);
      result['form'+n]=pixels(await page.locator('canvas').screenshot());
      fs.writeFileSync(path.join(output,`form-${n}.png`),await page.screenshot());
      assert(result['form'+n].lit>.06,'형태가 보이지 않습니다.');
    }
    before=await page.locator('canvas').screenshot();
    await page.mouse.move(720,420);await page.mouse.down();await page.mouse.move(900,480,{steps:12});await page.mouse.up();
    await page.waitForTimeout(100);
    assert(!before.equals(await page.locator('canvas').screenshot()),'드래그 시점 변경이 없습니다.');
    await page.getByRole('button',{name:'시점 초기화',exact:true}).click();
    await page.getByRole('button',{name:'소리 켜기',exact:true}).click();
    await page.getByRole('button',{name:'소리 끄기',exact:true}).click();
    await page.getByRole('combobox',{name:'렌더링 품질'}).selectOption('eco');
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'단일 HTML 다운로드'}).click()]);
    const standalone=path.join(output,'Field-Lab-07.html');await download.saveAs(standalone);
    await context.setOffline(true);await page.goto(pathToFileURL(standalone).href);
    await page.waitForFunction(()=>document.body.dataset.ready==='true');result.offline=pixels(await page.locator('canvas').screenshot());
    assert(result.offline.lit>.08,'다운로드한 HTML을 오프라인에서 표시하지 못했습니다.');
    for(const width of [390,320]){
      await page.setViewportSize({width,height:844});
      await page.getByRole('button',{name:'일시정지',exact:true}).click();
      await page.getByRole('button',{name:'형태 1',exact:true}).click();
      result['mobile'+width]=pixels(await page.locator('canvas').screenshot());
      fs.writeFileSync(path.join(output,`mobile-${width}.png`),await page.screenshot());
      assert(result['mobile'+width].lit>.05,'모바일 3D 화면이 비어 있습니다.');
      const overflow=await page.evaluate(()=>({body:document.documentElement.scrollWidth>innerWidth,controls:[...document.querySelectorAll('footer button,footer select,footer input')].some(e=>{const r=e.getBoundingClientRect();return r.left<0||r.right>innerWidth||r.bottom>innerHeight;})}));
      assert(!overflow.body&&!overflow.controls,'모바일 제어기가 화면을 벗어납니다.');
      await page.getByRole('button',{name:'재생',exact:true}).click();
    }
    await page.emulateMedia({reducedMotion:'reduce'});await page.reload();
    await page.waitForFunction(()=>document.body.dataset.ready==='true');
    assert.equal(await page.getByRole('button',{name:'재생',exact:true}).getAttribute('aria-pressed'),'true');
    assert.deepEqual(requests,[],'외부 네트워크 요청이 있습니다.');assert.deepEqual(errors,[],'브라우저 오류가 있습니다.');
    result.noExternalRequests=true;result.errors=errors;
    const lab=await context.newPage();
    await lab.route('**/snow-lab.js*',route=>route.abort());
    for(const width of [1440,390,320]){
      await lab.setViewportSize({width,height:844});
      await lab.goto(pathToFileURL(path.join(root,'snow-lab.html')).href);
      const entry=lab.getByRole('link',{name:'숨겨진 방 열기'});
      assert(await entry.isVisible(),'기존 이스터에그에 입구가 보이지 않습니다.');
      const rect=await entry.boundingBox();assert(rect.x>=0&&rect.x+rect.width<=width,'입구가 화면을 벗어납니다.');
    }
    await lab.getByRole('link',{name:'숨겨진 방 열기'}).click();
    await lab.waitForFunction(()=>document.body.dataset.ready==='true');
    result.labEntry=true;
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  } catch(error) {
    console.error(JSON.stringify({error:error.message,browserErrors:errors,result},null,2));
    await page.screenshot({path:path.join(output,'failure.png')});process.exitCode=1;
  } finally {await browser.close();}
})();
