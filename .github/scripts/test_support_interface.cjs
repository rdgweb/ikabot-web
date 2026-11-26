const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const rendered = spawnSync(process.env.TEST_PYTHON || 'python',[path.join(__dirname,'render_support_fixtures.py')],{
  cwd:root,encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',TEST_MARIADB:'0'},maxBuffer:15*1024*1024,
});
assert.equal(rendered.status,0,rendered.stderr);
const fixtures = JSON.parse(rendered.stdout);

async function setup(width, initial='empty') {
  const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width,height:1000}});
  // Keep layout assertions and screenshots independent of transition snapshots.
  await page.addInitScript(()=>{document.startViewTransition=undefined;});
  page.setDefaultTimeout(10000);
  const state={stage:initial,conflict:false,posts:[],requests:[],errors:[]};
  page.on('pageerror',error=>state.errors.push(error.message));
  await page.route('**/*',async route=>{
    const request=route.request(), url=new URL(request.url());
    if(url.hostname!=='support.test')return route.abort();
    if(url.pathname.startsWith('/static/')){
      const file=path.resolve(root,'hub_v2',url.pathname.slice(1));
      if(!file.startsWith(path.join(root,'hub_v2/static')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'application/octet-stream'});
    }
    const hx=request.headers()['hx-request']==='true';
    if(url.pathname.startsWith('/suporte/'))state.requests.push([request.method(),url.pathname,hx]);
    if(request.method()==='POST' && url.pathname.startsWith('/suporte/')){
      const data=Object.fromEntries(new URLSearchParams(request.postData()));
      state.posts.push(data);
      if(url.pathname==='/suporte/novo/')state.stage='customer_open';
      else if(state.conflict){state.conflict=false;return route.fulfill({status:409,contentType:'text/html',body:fixtures['stale'+(hx?'_hx':'')]});}
      else if(data.action==='take')state.stage='team_assigned';
      else if(data.action==='reply')state.stage=url.pathname.includes('/atendimento/')?'team_waiting':'customer_replied';
      else if(data.status==='resolved')state.stage='customer_resolved';
      // Fulfill the final GET after Django's redirect. Playwright routing does not
      // re-intercept redirect chains; the server tests assert the real 302 flow.
      const destination=url.pathname==='/suporte/novo/'?fixtures.customer_url:url.pathname;
      return route.fulfill({status:200,headers:{'HX-Push-Url':destination},contentType:'text/html',body:fixtures[state.stage+(hx?'_hx':'')]});
    }
    let key=url.pathname==='/suporte/novo/'?'create':url.pathname==='/suporte/atendimento/'?'queue':
      url.pathname===fixtures.queue_url?(state.stage.startsWith('team_')?state.stage:'team_open'):
      url.pathname===fixtures.customer_url?state.stage:state.stage;
    if(!url.pathname.startsWith('/suporte/'))return route.fulfill({status:204,body:''});
    return route.fulfill({contentType:'text/html',body:fixtures[key+(hx?'_hx':'')]});
  });
  return {browser,page,state};
}

test('customer opens, follows and resolves support tickets on desktop and mobile',async()=>{
  for(const width of [1440,390]){
    const {browser,page,state}=await setup(width);
    try{
      await page.goto('http://support.test/suporte/');
      if(width<800)await page.locator('#sidebar-toggle-btn').click();
      await page.getByRole('link',{name:'Abrir chamado',exact:true}).click();
      await page.getByLabel('Assunto',{exact:true}).fill('Ajuda com conexão do agente');
      await page.getByLabel('Categoria',{exact:true}).selectOption('agent');
      await page.getByLabel('O que aconteceu?',{exact:true}).fill('O agente está desconectado e preciso de ajuda para configurar a conexão.');
      await page.getByLabel('Compartilhar a página relacionada',{exact:false}).check();
      await page.getByLabel('Página relacionada (opcional)',{exact:true}).fill('/accounts/nodes/?secret=ignored');
      await page.getByRole('button',{name:'Enviar chamado',exact:true}).click();
      await page.getByText(fixtures.code,{exact:false}).first().waitFor();
      assert.equal(state.posts[0].category,'agent');
      assert.ok(state.posts[0].request_id);
      state.conflict=true;
      await page.getByLabel('Sua resposta',{exact:true}).fill('Meu texto continua aqui.');
      await page.getByRole('button',{name:'Enviar resposta',exact:true}).click();
      await page.getByRole('alert').filter({hasText:'recebeu outra atualização'}).waitFor();
      assert.equal(await page.getByLabel('Sua resposta',{exact:true}).inputValue(),'Meu texto continua aqui.');
      await page.getByRole('button',{name:'Enviar resposta',exact:true}).click();
      await page.getByText('Conferi a conexão e agora o agente está online.',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Marcar como resolvido',exact:true}).click();
      await page.getByRole('button',{name:'Reabrir chamado',exact:true}).waitFor();
      await page.evaluate(async()=>{document.getAnimations().forEach(animation=>animation.cancel());await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);});
      if(process.env.SUPPORT_SCREENSHOT_DIR){fs.mkdirSync(process.env.SUPPORT_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.SUPPORT_SCREENSHOT_DIR,`support-${width}.png`),fullPage:true});}
      const overflow=await page.evaluate(()=>[...document.querySelectorAll('main *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1).map(el=>[el.tagName,el.className,Math.round(el.getBoundingClientRect().right)]).slice(0,12));
      assert.equal(await page.locator('body').evaluate(el=>el.scrollWidth>innerWidth),false,JSON.stringify(overflow));
      await page.evaluate(()=>document.body.classList.add('ik-private'));
      await page.waitForFunction(()=>getComputedStyle(document.querySelector('.sup-message-body')).filter==='blur(6px)');
      await page.evaluate(()=>document.body.classList.remove('ik-private'));
    }catch(error){console.error('support customer state',width,state.stage,state.posts.map(p=>p.action||'create'),page.url());throw error;}finally{await browser.close();}
  }
});

test('support queue can assume and answer while readers get a read-only conversation',async()=>{
  const {browser,page,state}=await setup(1440,'queue');
  try{
    await page.goto('http://support.test/suporte/atendimento/');
    await page.getByRole('link').filter({hasText:'Ajuda com conexão do agente'}).click();
    await page.getByRole('button',{name:'Assumir atendimento',exact:true}).click();
    await page.getByRole('button',{name:'Liberar atendimento',exact:true}).waitFor();
    await page.getByLabel('Sua resposta',{exact:true}).fill('Confira a conexão do agente com o Hub e nos informe o resultado.');
    await page.getByRole('button',{name:'Enviar resposta',exact:true}).click();
    await page.getByText('Confira a conexão do agente com o Hub e nos informe o resultado.',{exact:true}).waitFor();
    assert.equal(state.posts[0].action,'take');
    state.stage='readonly';
    await page.goto('http://support.test'+fixtures.customer_url);
    assert.equal(await page.getByRole('button',{name:'Enviar resposta',exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'Marcar como resolvido',exact:true}).count(),0);
  }catch(error){console.error('support queue state',state.stage,state.posts.map(p=>p.action),state.requests,state.errors);throw error;}finally{await browser.close();}
});
