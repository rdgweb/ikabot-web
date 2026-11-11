// Browser regression for N-166, using the shipped template, HTMX and Alpine.
// Run with Playwright available in NODE_PATH (see docker-publish.yml).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, 'hub_v2', file), 'utf8');
const template = read('apps/notes/templates/notes/changelog.html');
const form = template.match(/<form method="post"[\s\S]*?<\/form>/)[0]
  .replace(/{% url 'accounts:node-request-update' node.pk %}/g, '/update')
  .replace(/{% csrf_token %}/g, '<input type="hidden" name="csrfmiddlewaretoken" value="test-token">')
  .replace(/{{ node.name\|escapejs }}/g, 'blackshadow-test')
  .replace(/{{ node.agent_version\|escapejs }}/g, '0.1.1')
  .replace(/{{ agent_release.version\|escapejs }}/g, '0.1.2');
const modal = read('templates/components/confirm_dialog.html')
  .split('<div x-data="confirmDialog()"')[1].replace(/{% endif %}/g, '');

for (const navigation of ['direct', 'htmx']) {
  test(`agent update waits for confirmation after ${navigation} navigation`, async () => {
    const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
    try {
      const page = await browser.newPage();
      const updates = [];
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('http://confirmation.test/**', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/update') {
          updates.push(route.request());
          return route.fulfill({ status: 200, contentType: 'text/html', body: '<p>Update accepted</p>' });
        }
        if (url.pathname.startsWith('/static/')) {
          return route.fulfill({ contentType: 'text/javascript', body: read(url.pathname.slice(1)) });
        }
        if (url.pathname === '/updates') {
          return route.fulfill({ contentType: 'text/html', body: form });
        }
        await route.fulfill({ contentType: 'text/html', body: `<!doctype html>
          <head><script src="/static/vendor/htmx.min.js"></script>
          <script src="/static/vendor/alpine.min.js" defer></script></head>
          <body x-data="{}" hx-boost="true" hx-target="#page-content" hx-swap="innerHTML">
          <div id="page-content">${navigation === 'direct' ? form : '<a href="/updates">Updates</a>'}</div>
          <div x-data="confirmDialog()"${modal}
          <script src="/static/js/app.js"></script></body>` });
      });
      await page.goto('http://confirmation.test/');
      if (navigation === 'htmx') await page.getByRole('link', { name: 'Updates' }).click();
      const dialog = page.locator('[x-data="confirmDialog()"]');
      await page.getByRole('button', { name: 'Atualizar pelo Hub' }).click();
      await dialog.waitFor({ state: 'visible' });
      await page.waitForTimeout(200);
      assert.equal(updates.length, 0, 'opening the dialog must not send an update');
      await dialog.getByRole('button', { name: 'Cancelar' }).click();
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(updates.length, 0, 'cancel must not send an update');
      // Keyboard submission follows the same confirmation gate.
      await page.getByRole('button', { name: 'Atualizar pelo Hub' }).focus();
      await page.keyboard.press('Enter');
      await dialog.waitFor({ state: 'visible' });
      assert.equal(updates.length, 0);
      await dialog.getByRole('button', { name: 'Atualizar agente', exact: true }).click();
      await page.waitForURL('**/update');
      assert.equal(updates.length, 1, 'confirm must send exactly one update');
      assert.equal(updates[0].method(), 'POST');
      assert.match(updates[0].postData(), /csrfmiddlewaretoken=test-token/);
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  });
}
