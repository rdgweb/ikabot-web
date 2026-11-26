const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname,'../..');
const rendered=spawnSync(process.env.TEST_PYTHON||'python',[path.join(__dirname,'render_mobile_fixtures.py')],{
  cwd:root,encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',TEST_MARIADB:'0'},maxBuffer:30*1024*1024,
});
assert.equal(rendered.status,0,rendered.stderr);
const fixtures=JSON.parse(rendered.stdout);
async function capture(page,name){
  if(!process.env.MOBILE_SCREENSHOT_DIR)return;
  fs.mkdirSync(process.env.MOBILE_SCREENSHOT_DIR,{recursive:true});
  await page.screenshot({path:path.join(process.env.MOBILE_SCREENSHOT_DIR,name+'.png'),fullPage:true,animations:'disabled'});
}

async function setup(width, preference=null, noStorage=false) {
  const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width,height:900}});
  page.setDefaultTimeout(10000);
  await page.addInitScript(({preference,noStorage,font})=>{
    document.startViewTransition=undefined;
    if(font)document.addEventListener('DOMContentLoaded',()=>document.body.style.fontFamily=font,{once:true});
    if(noStorage){Storage.prototype.getItem=()=>{throw new Error('Storage disabled');};Storage.prototype.setItem=()=>{throw new Error('Storage disabled');};}
    else if(preference!==null && !sessionStorage.getItem('mobile-test-initialized')){
      localStorage.setItem('sidebar-collapsed',preference);sessionStorage.setItem('mobile-test-initialized','1');
    }
  },{preference,noStorage,font:process.env.MOBILE_TEST_FONT||null});
  const state={failNavigation:false,form:'1002'};
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.hostname!=='mobile.test')return route.abort();
    if(url.pathname.startsWith('/static/')){
      const file=path.resolve(root,'hub_v2',url.pathname.slice(1));
      if(!file.startsWith(path.join(root,'hub_v2/static')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'application/octet-stream'});
    }
    if(url.pathname==='/api/schema/')return route.fulfill({contentType:'application/json',body:JSON.stringify(fixtures.schema)});
    if(url.pathname==='/jobs/new/')return route.fulfill({contentType:'text/html',body:fixtures.job_forms[state.form].html});
    const key=url.pathname+url.search;
    if(!fixtures.pages[key])return route.fulfill({status:204,body:''});
    const hx=request.headers()['hx-request']==='true';
    if(hx&&state.failNavigation)return route.fulfill({status:500,body:'Test navigation failure'});
    return route.fulfill({contentType:'text/html',body:(hx?fixtures.partials:fixtures.pages)[key]});
  });
  return {browser,page,state};
}

test('mobile drawer is accessible, closes on successful navigation and keeps failed navigation usable',async()=>{
  const {browser,page,state}=await setup(390);
  try{
    await page.goto('http://mobile.test/suporte/');
    const sidebar=page.locator('#main-sidebar');
    const opener=page.getByRole('button',{name:'Abrir menu de navegação',exact:true});
    assert.equal(await sidebar.evaluate(el=>el.inert),true);
    assert.equal(await page.locator('.main-content').evaluate(el=>parseFloat(getComputedStyle(el).marginLeft)),0);
    await opener.click();
    await page.getByRole('dialog',{name:'Menu principal'}).waitFor();
    await page.waitForFunction(()=>document.activeElement?.id==='sidebar-toggle-btn');
    await capture(page,'mobile-menu');
    assert.equal(await page.locator('.main-content').evaluate(el=>el.inert),true);
    assert.equal(await page.getByRole('button',{name:'Fechar menu',exact:true}).evaluate(el=>el===document.activeElement),true);
    await page.keyboard.press('Shift+Tab');
    assert.equal(await sidebar.evaluate(el=>el.contains(document.activeElement)),true);
    await page.keyboard.press('Tab');
    await page.keyboard.press('Escape');
    assert.equal(await sidebar.evaluate(el=>el.inert),true);
    assert.equal(await opener.evaluate(el=>el===document.activeElement),true);
    await opener.click();
    await page.locator('#sidebar-backdrop').click({position:{x:380,y:350}});
    assert.equal(await sidebar.evaluate(el=>el.inert),true);
    await opener.click();
    state.failNavigation=true;
    const failedResponse=page.waitForResponse(r=>r.url().endsWith('/organizacao/')&&r.status()===500);
    await page.locator('#main-sidebar a[href="/organizacao/"]').click();
    await failedResponse;
    assert.equal(await sidebar.evaluate(el=>el.inert),false);
    state.failNavigation=false;
    const response=page.waitForResponse(r=>r.url().endsWith('/organizacao/')&&r.status()===200);
    await page.locator('#main-sidebar a[href="/organizacao/"]').click();
    await response;
    await page.waitForFunction(()=>!document.body.classList.contains('sidebar-mobile-open'));
    assert.equal(await page.locator('.main-content').evaluate(el=>el.inert),false);
    assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'),'false');
    assert.equal(await page.locator('#main-area').evaluate(el=>el.contains(document.activeElement)),true);
  }catch(error){console.error('drawer diagnostics',await page.evaluate(()=>({active:[document.activeElement?.tagName,document.activeElement?.id],open:document.body.className,sidebarInert:document.getElementById('main-sidebar').inert,mainInert:document.querySelector('.main-content').inert})));throw error;}finally{await browser.close();}
});

test('desktop preference survives mobile use, reload and breakpoint changes',async()=>{
  const {browser,page}=await setup(1440,'true');
  try{
    await page.goto('http://mobile.test/suporte/');
    assert.equal(await page.locator('body').evaluate(el=>el.classList.contains('sidebar-collapsed')),true);
    await page.getByRole('button',{name:'Expandir menu',exact:true}).click();
    assert.equal(await page.evaluate(()=>localStorage.getItem('sidebar-collapsed')),'false');
    await page.setViewportSize({width:390,height:900});
    await page.getByRole('button',{name:'Abrir menu de navegação',exact:true}).click();
    await page.getByRole('dialog',{name:'Menu principal'}).waitFor();
    await page.setViewportSize({width:1440,height:900});
    await page.waitForFunction(()=>!document.body.classList.contains('sidebar-mobile-open'));
    assert.equal(await page.locator('.main-content').evaluate(el=>el.inert),false);
    assert.equal(await page.evaluate(()=>localStorage.getItem('sidebar-collapsed')),'false');
    await page.getByRole('button',{name:'Recolher menu',exact:true}).click();
    await page.setViewportSize({width:390,height:900});
    await page.reload();
    assert.equal(await page.locator('#main-sidebar').evaluate(el=>el.inert),true);
    await page.setViewportSize({width:1440,height:900});
    await page.waitForFunction(()=>document.body.classList.contains('sidebar-collapsed'));
    assert.equal(await page.locator('body').evaluate(el=>el.classList.contains('sidebar-collapsed')),true);
  }finally{await browser.close();}
  const isolated=await setup(320,null,true);
  try{
    await isolated.page.emulateMedia({reducedMotion:'reduce'});
    await isolated.page.goto('http://mobile.test/suporte/');
    await isolated.page.getByRole('button',{name:'Abrir menu de navegação',exact:true}).click();
    await isolated.page.waitForFunction(()=>document.activeElement?.id==='sidebar-toggle-btn');
    await isolated.page.getByRole('button',{name:'Fechar menu',exact:true}).click();
    assert.equal(await isolated.page.locator('.main-content').evaluate(el=>el.inert),false);
  }finally{await isolated.browser.close();}
});

test('all menu pages and representative forms fit phone viewports',async()=>{
  assert.ok(Object.values(fixtures.status).every(status=>status===200),JSON.stringify(fixtures.status));
  const failures=[];
  for(const width of [320,390,768]){
    const {browser,page,state}=await setup(width);
    try{
      for(const target of Object.keys(fixtures.pages)){
        await page.goto('http://mobile.test'+target);
        await page.evaluate(async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);document.getAnimations().forEach(a=>a.cancel());});
        const report=await page.evaluate(()=>({width:innerWidth,actual:Math.max(document.body.scrollWidth,document.documentElement.scrollWidth),
          elements:[...document.querySelectorAll('#main-area *')].filter(el=>{
            if(!el.getClientRects().length||el.getBoundingClientRect().right<=innerWidth+2)return false;
            for(let parent=el.parentElement;parent&&parent!==document.body;parent=parent.parentElement){
              if(['auto','scroll','hidden','clip'].includes(getComputedStyle(parent).overflowX)&&parent.getBoundingClientRect().right<=innerWidth+2)return false;
            }
            return true;
          })
            .slice(0,8).map(el=>({tag:el.tagName,class:el.className,text:(el.innerText||'').slice(0,70),right:Math.round(el.getBoundingClientRect().right)}))}));
        if(report.actual>width+2)failures.push({width,target,...report});
        if(width===390 && target==='/')await capture(page,'mobile-dashboard');
        if(width===390 && target==='/suporte/')await capture(page,'mobile-support');
        if(width===390 && /^\/profiles\/presets\/[a-f0-9-]+\/$/.test(target))await capture(page,'mobile-preset');
      }
      await page.goto('http://mobile.test/');
      for(const [action,form] of Object.entries(fixtures.job_forms)){
        assert.equal(form.status,200);
        state.form=action;
        await page.getByRole('button',{name:'Novo Job',exact:true}).click();
        await page.locator('#job-modal-body form').first().waitFor();
        const bounds=await page.locator('#job-modal-body').evaluate(el=>({left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right,width:el.clientWidth,scroll:el.scrollWidth}));
        if(bounds.left<0||bounds.right>width+2||bounds.scroll>bounds.width+2)failures.push({width,target:'job-form-'+action,...bounds});
        await page.keyboard.press('Escape');
      }
      for(const target of ['/jobs/','/accounts/nodes/','/proxy/','/notes/']){
        await page.goto('http://mobile.test'+target);
        await page.getByRole('button',{name:/Filtros/}).first().click();
        const panel=page.locator('.filter-panel:visible').first();
        await panel.waitFor();
        const bounds=await panel.boundingBox();
        if(bounds.x<0||bounds.x+bounds.width>width+2)failures.push({viewport:width,target:target+'filters',...bounds});
        if(width===390 && target==='/jobs/')await capture(page,'mobile-filters');
      }
    }finally{await browser.close();}
  }
  if(process.env.MOBILE_AUDIT_REPORT){fs.mkdirSync(path.dirname(process.env.MOBILE_AUDIT_REPORT),{recursive:true});fs.writeFileSync(process.env.MOBILE_AUDIT_REPORT,JSON.stringify(failures,null,2));}
  assert.equal(failures.length,0,JSON.stringify(failures));
});
