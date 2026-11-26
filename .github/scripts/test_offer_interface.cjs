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
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if(url.hostname !== 'offer.test')return route.abort();
    if(url.pathname.startsWith('/static/')) {
      const file = path.resolve(root, 'hub_v2', url.pathname.slice(1));
      if(!file.startsWith(path.join(root,'hub_v2/static')+path.sep) || !fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'application/octet-stream'});
    }
    const key = url.pathname.includes('/plano/') ? 'checkout' : url.pathname.includes('/cadastro/') ? 'signup'
      : url.pathname.startsWith('/en/') ? 'english' : url.searchParams.get('platform_agents') === '2' ? 'invalid'
      : url.searchParams.has('estimate') ? 'six' : 'default';
    return route.fulfill({contentType:'text/html',body:fixtures[key]});
  });
  return {browser,page};
}

test('monthly estimate keeps plan and lobby add-ons separate on desktop and mobile', async () => {
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
    const {browser,page} = await setup(viewport);
    try {
      await page.goto('http://offer.test/pt/precos/');
      await page.getByLabel('Personagens ativos (subcontas)',{exact:true}).fill('6');
      await page.getByLabel('Contas GameForge (lobbies)',{exact:true}).fill('6');
      await page.getByLabel('Lobbies com agente da plataforma',{exact:true}).fill('6');
      await page.getByLabel('Lobbies com pacote de proxies da plataforma',{exact:true}).fill('6');
      await page.getByRole('button',{name:'Calcular estimativa'}).click();
      await page.waitForURL(/platform_agents=6/);
      await page.getByText('R$ 148,70',{exact:false}).waitFor();
      assert.ok(await page.getByText('R$ 89,40',{exact:true}).isVisible());
      assert.equal(await page.locator('body').evaluate(el => el.scrollWidth > innerWidth),false);
      if(process.env.OFFER_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.OFFER_SCREENSHOT_DIR,{recursive:true});
        await page.screenshot({path:path.join(process.env.OFFER_SCREENSHOT_DIR,`offer-${viewport.width}.png`),fullPage:true});
      }
    } finally {await browser.close();}
  }
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
