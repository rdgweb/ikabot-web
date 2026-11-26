const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const rendered = spawnSync(process.env.TEST_PYTHON || 'python', [path.join(__dirname, 'render_offer_fixtures.py')], {
  cwd: root, encoding:'utf8', env:{...process.env, PYTHONDONTWRITEBYTECODE:'1', TEST_MARIADB:'0'}, maxBuffer:10*1024*1024,
});
assert.equal(rendered.status, 0, rendered.stderr);
const fixtures = JSON.parse(rendered.stdout);

async function setup(viewport) {
  const browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
  const page = await browser.newPage({viewport});
  const state = {fail:false, requests:[]};
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if(url.hostname !== 'offer.test')return route.abort();
    if(url.pathname.startsWith('/static/')) {
      const file = path.resolve(root, 'hub_v2', url.pathname.slice(1));
      if(!file.startsWith(path.join(root,'hub_v2/static')+path.sep) || !fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'application/octet-stream'});
    }
    const partial = route.request().headers()['hx-request'] === 'true';
    if (partial) {
      state.requests.push(url.search);
      if(state.fail)return route.abort();
    }
    const key = url.pathname.includes('/plano/') ? 'checkout' : url.pathname.includes('/cadastro/') ? 'signup'
      : partial && url.searchParams.get('platform_proxies') === '' ? 'empty'
      : partial && url.searchParams.get('platform_agents') === '1' && url.searchParams.get('platform_proxies') === '1' ? 'one'
      : url.pathname.startsWith('/en/') ? 'english' : url.searchParams.get('platform_agents') === '2' ? 'invalid'
      : url.searchParams.has('estimate') ? 'six' : 'default';
    return route.fulfill({contentType:'text/html',body:fixtures[key + (partial ? '_partial' : '')]});
  });
  return {browser,page,state};
}

test('estimate updates automatically without navigation, scroll jumps or losing focus', async () => {
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
    const {browser,page,state} = await setup(viewport);
    try {
      await page.goto('http://offer.test/pt/precos/');
      await page.getByLabel('Personagens ativos (subcontas)',{exact:true}).fill('6');
      await page.getByLabel('Contas GameForge (lobbies)',{exact:true}).fill('6');
      await page.getByLabel('Lobbies com agente da plataforma',{exact:true}).fill('6');
      const updated = page.waitForResponse(response => response.request().headers()['hx-request'] === 'true' && response.url().includes('platform_proxies=6'));
      await page.getByLabel('Lobbies com pacote de proxies da plataforma',{exact:true}).fill('6');
      await updated;
      assert.equal(await page.getByRole('button',{name:'Calcular estimativa'}).count(),0);
      await page.getByText('R$ 148,70',{exact:false}).waitFor();
      assert.equal(page.url(),'http://offer.test/pt/precos/');
      assert.ok(state.requests.some(query => query.includes('platform_proxies=6')));
      assert.ok(await page.getByText('R$ 89,40',{exact:true}).isVisible());
      await page.getByLabel('Lobbies com agente da plataforma',{exact:true}).fill('1');
      const proxy = page.getByLabel('Lobbies com pacote de proxies da plataforma',{exact:true});
      await proxy.focus();
      await page.evaluate(() => document.documentElement.style.scrollBehavior = 'auto');
      await proxy.scrollIntoViewIfNeeded();
      const y = await page.evaluate(() => scrollY);
      await proxy.fill('1');
      await page.getByText('R$ 49,70',{exact:false}).waitFor();
      assert.equal(await proxy.evaluate(el => el === document.activeElement),true);
      assert.ok(Math.abs(await page.evaluate(() => scrollY) - y) <= 1);
      assert.equal(page.url(),'http://offer.test/pt/precos/');
      const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')]
        .filter(el => el.getBoundingClientRect().right > innerWidth + 1)
        .map(el => `${el.tagName}.${el.className}: ${Math.round(el.getBoundingClientRect().right)}`).slice(0, 12));
      assert.equal(await page.locator('body').evaluate(el => el.scrollWidth > innerWidth),false, JSON.stringify({viewport,overflow}));
      if(process.env.OFFER_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.OFFER_SCREENSHOT_DIR,{recursive:true});
        await page.screenshot({path:path.join(process.env.OFFER_SCREENSHOT_DIR,`offer-${viewport.width}.png`),fullPage:true});
      }
    } finally {await browser.close();}
  }
});

test('empty values and connection failures hide stale totals and recover on editing', async () => {
  const {browser,page,state} = await setup({width:390,height:844});
  try {
    await page.goto('http://offer.test/pt/precos/?estimate=1');
    const proxy = page.getByLabel('Lobbies com pacote de proxies da plataforma',{exact:true});
    await proxy.fill('');
    await page.getByText('Informe uma quantidade.',{exact:false}).waitFor();
    assert.equal(await page.locator('#estimate-result .cost-breakdown').count(),0);
    state.fail = true;
    await proxy.fill('1');
    await page.locator('#estimate-network-error').waitFor();
    assert.equal(await page.locator('#estimate-result').textContent(),'');
    state.fail = false;
    await page.getByLabel('Lobbies com agente da plataforma',{exact:true}).fill('1');
    await proxy.fill('0');
    await proxy.fill('1');
    await page.getByText('R$ 49,70',{exact:false}).waitFor();
    assert.equal(await page.locator('#estimate-network-error').isVisible(),false);
  } finally {await browser.close();}
});

test('invalid estimate, English BRL prices and checkout disclosure stay clear', async () => {
  const {browser,page} = await setup({width:1440,height:1000});
  try {
    await page.goto('http://offer.test/pt/precos/?platform_agents=2');
    assert.ok(await page.getByText('A quantidade não pode exceder os lobbies.',{exact:false}).isVisible());
    await page.goto('http://offer.test/en/pricing/');
    assert.ok(await page.getByText('R$ 148,70',{exact:false}).isVisible());
    assert.equal(await page.getByText('US$',{exact:false}).count(),0);
    await page.goto('http://offer.test/organizacao/plano/');
    assert.ok(await page.getByText('checkout cobra a faixa do plano',{exact:false}).isVisible());
    assert.ok(await page.getByText('R$ 148,70',{exact:false}).isVisible());
  } finally {await browser.close();}
});
