const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const rendered = spawnSync(process.env.TEST_PYTHON || 'python', [path.join(__dirname, 'render_access_fixtures.py')], {
  cwd: root, encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', TEST_MARIADB: '0' }, maxBuffer: 10 * 1024 * 1024,
});
assert.equal(rendered.status, 0, rendered.stderr);
const fixtures = JSON.parse(rendered.stdout);

test('Scalar loads locally with typed OpenAPI and no remote requests', async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const external = [], errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'access.test') { external.push(url.href); return route.abort(); }
      if (url.pathname === '/api/docs/') return route.fulfill({contentType:'text/html',body:fixtures.docs});
      if (url.pathname === '/api/schema/') return route.fulfill({contentType:'application/json',body:JSON.stringify(fixtures.schema)});
      if (url.pathname.endsWith('/standalone.js')) return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'hub_v2/static/vendor/scalar/1.83.0/standalone.js'))});
      return route.fulfill({status:204,body:''});
    });
    await page.goto('http://access.test/api/docs/');
    await page.getByRole('heading', {name:'ikabot hub Agent API',exact:true}).waitFor();
    await page.getByText('Models', {exact:true}).first().waitFor();
    await page.waitForTimeout(300);
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    assert.equal(await page.getByText('Developer Tools',{exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'Ask AI',exact:true}).count(),0);
    assert.equal(await page.locator('body').evaluate(el=>el.scrollWidth > innerWidth),false);
  } finally { await browser.close(); }
});

test('Access pages render searches, roles, provenance and explicit confirmation', async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'access.test') return route.abort();
      if (url.pathname.startsWith('/static/')) {
        const file=path.resolve(root,'hub_v2',url.pathname.slice(1));
        if (!file.startsWith(path.join(root,'hub_v2/static')+path.sep) || !fs.existsSync(file)) return route.fulfill({status:404,body:''});
        return route.fulfill({contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'application/octet-stream',body:fs.readFileSync(file)});
      }
      const name=url.searchParams.get('fixture');
      return route.fulfill(name && fixtures[name] ? {contentType:'text/html',body:fixtures[name].replace(' data-autostart','')} : {status:204,body:''});
    });
    for (const name of ['people','roles','person','preview']) {
      await page.goto('http://access.test/users/permissoes/?fixture='+name);
      assert.equal(await page.locator('a[href="/api/docs/"]').getAttribute('hx-boost'),'false');
      if(name==='roles')assert.ok(await page.getByText('Criar papel',{exact:true}).isVisible());
      if(name==='person')assert.ok(await page.getByText('Acesso efetivo · browser-person',{exact:true}).isVisible());
      if(name==='preview') {
        assert.ok(await page.getByRole('button',{name:'Confirmar alteração',exact:true}).isVisible());
        assert.ok(await page.locator('input[name="confirmation"]').inputValue());
        assert.ok(await page.getByText('browser-person',{exact:false}).first().isVisible());
      }
      assert.equal(await page.locator('body').evaluate(el=>el.scrollWidth > innerWidth),false,name);
    }
  } finally { await browser.close(); }
});
