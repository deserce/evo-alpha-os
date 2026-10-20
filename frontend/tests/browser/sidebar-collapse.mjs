// Read-only production regression for grouped daily hints. Isolated CDP 9347.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const base = process.env.APP_BASE_URL || 'http://127.0.0.1:3000';
const date = process.env.TEST_TRADE_DATE || '2026-09-30';
const archiveResponse = await fetch(`${base}/api/v1/watch/mrgc-report?trade_date=${date}`);
assert.ok(archiveResponse.ok, 'Sample archive readable');
const archive = (await archiveResponse.json()).data;
assert.ok(archive?.archive?.complete && archive?.report, 'Require complete existing archive; do not generate');
const targets = await (await fetch('http://127.0.0.1:9347/json/list')).json();
const target = targets.find(item => item.type === 'page');
assert.ok(target, 'CDP 9347 requires an isolated browser page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.addEventListener('open', resolve, { once: true }); ws.addEventListener('error', reject, { once: true }); });
let seq = 0;
const pending = new Map();
const errors = [];
const requests = [];
ws.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) {
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    message.error ? request.reject(Error(message.error.message)) : request.resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  else if (message.method === 'Network.requestWillBeSent') {
    const { request, timestamp } = message.params;
    requests.push({ method: request.method, url: request.url, timestamp });
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params }));
});
const evaluate = async expression => {
  const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw Error(response.exceptionDetails.text);
  return response.result.value;
};
const wait = async expression => {
  for (let attempt = 0; attempt < 300; attempt++) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error(`UI timeout: ${expression}`);
};
const sidebar = 'document.querySelector("[data-stock-sidebar]")';
const click = async (label, scope = sidebar, prefix = false) => {
  const matched = await evaluate(`(()=>{const root=${scope};const button=[...root.querySelectorAll('button')].find(b=>b.getClientRects().length && ${prefix ? 'b.textContent.trim().startsWith' : 'b.textContent.trim()==='}${prefix ? '(' : ''}${JSON.stringify(label)}${prefix ? ')' : ''});button?.click();return Boolean(button);})()`);
  assert.ok(matched, `Visible button ${label}`);
};
const tab = async label => {
  await click(label);
  await wait(`${sidebar}?.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim()===${JSON.stringify(label)}`);
};
const setScroll = async selector => {
  await wait(`document.querySelector(${JSON.stringify(selector)})?.clientHeight>0`);
  return evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollTop=Math.min(100,Math.max(0,e.scrollHeight-e.clientHeight));e.dispatchEvent(new Event('scroll'));return e.scrollTop;})()`);
};
const checkScroll = async (selector, before) => {
  await wait(`document.querySelector(${JSON.stringify(selector)})?.clientHeight>0`);
  await new Promise(resolve => setTimeout(resolve, 350));
  const actual = await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollTop`);
  assert.ok(Math.abs(actual-before)<=30, `${selector} scroll ${before} -> ${actual}`);
};
const tabs=['个股资料','AI 助手','当日提示'];
const scrollSelectors={'个股资料':'[data-sidebar-info-scroll]','AI 助手':'[data-sidebar-ai-scroll]','当日提示':'[data-sidebar-scroll]'};
const measurements=[];
const toggle=async label=>{
  assert.ok(await evaluate(`(()=>{const b=${sidebar}.querySelector('[aria-label="${label}"]');if(!b||!b.getClientRects().length)return false;b.click();return true;})()`),`Visible shared ${label} button`);
};
const geometry=()=>evaluate(`(()=>{const p=${sidebar};return {width:p.getBoundingClientRect().width,chartWidth:document.querySelector('[data-workspace-chart]').getBoundingClientRect().width,collapsed:p.dataset.sidebarCollapsed,visibleTabs:[...p.querySelectorAll('[role="tab"]')].filter(e=>e.getClientRects().length).map(e=>e.textContent.trim()),visibleButtons:[...p.querySelectorAll('button')].filter(e=>e.getClientRects().length).map(e=>e.getAttribute('aria-label')||e.textContent.trim()),selected:[...p.querySelectorAll('[role="tab"][aria-selected="true"]')].map(e=>e.textContent.trim())};})()`);
const assertCollapsed=async label=>{
  await wait(`${sidebar}?.dataset.sidebarCollapsed==='true'`);
  const g=await geometry();
  assert.ok(Math.abs(g.width-44)<=1,`Collapsed sidebar is 44px, got ${g.width}`);
  assert.deepEqual(g.visibleTabs,[],'All three tabs hidden when collapsed');
  assert.deepEqual(g.visibleButtons,['展开右侧栏'],'Only expansion rail remains visible');
  assert.deepEqual(g.selected,[label],'Collapsed sidebar retains active tab state');
  return g;
};
const goDetail=async()=>{
  const symbol=await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`);
  assert.ok(await evaluate(`(()=>{const link=[...document.querySelectorAll('a')].find(a=>a.textContent.trim()==='个股详情'&&a.getClientRects().length);link?.click();return Boolean(link);})()`),'Actual detail Next Link');
  await wait(`location.pathname==='/a/stock/${symbol}'&&${sidebar}`);
};
try{
  await send('Runtime.enable');await send('Page.enable');await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:`${base}/a/watchlist?version=sidebar-collapse`});
  await wait(`${sidebar} && document.querySelector('[data-selected-symbol]')`);
  assert.ok(await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>e.querySelector('option[value="${date}"]'));if(!s)return false;s.value='${date}';s.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`),'Actual date select');
  if(await evaluate(`${sidebar}.dataset.sidebarCollapsed==='true'`))await toggle('展开右侧栏');
  for(const label of tabs){
    await tab(label);
    const selector=scrollSelectors[label];
    await wait(`document.querySelector(${JSON.stringify(selector)})?.clientHeight>0`);
    if(label==='AI 助手'){
      await wait(`document.querySelector('[data-daily-assistant]')?.textContent.includes('完整存档')`);
      await click('问助手');
      await wait(`document.querySelector('[aria-label="向助手提问"]')`);
      await evaluate(`(()=>{const e=document.querySelector('[aria-label="向助手提问"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'收起侧栏时保留的分析草稿');e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      await wait(`document.querySelector('[aria-label="向助手提问"]').value==='收起侧栏时保留的分析草稿'`);
    }
    const scroll=await setScroll(selector);const expanded=await geometry();
    assert.ok(Math.abs(expanded.width-360)<=1,'Expanded sidebar is 360px at 1600 viewport');
    await toggle('收起右侧栏');const collapsed=await assertCollapsed(label);
    assert.ok(collapsed.chartWidth-expanded.chartWidth>=250,'Collapse gives chart at least 250px additional width');
    await toggle('展开右侧栏');
    await wait(`${sidebar}.dataset.sidebarCollapsed==='false'`);
    const restored=await geometry();
    assert.ok(Math.abs(restored.width-expanded.width)<=1,'Expansion restores sidebar width');
    assert.deepEqual(restored.selected,[label],'Expansion restores selected tab');
    await checkScroll(selector,scroll);
    if(label==='AI 助手')assert.equal(await evaluate(`document.querySelector('[aria-label="向助手提问"]').value`),'收起侧栏时保留的分析草稿','Collapse preserves AI question draft');
    await toggle('收起右侧栏');await goDetail();await assertCollapsed(label);
    await click('返回','document');
    await wait(`location.pathname==='/a/watchlist'&&${sidebar}`);
    await assertCollapsed(label);
    await toggle('展开右侧栏');
    await wait(`${sidebar}.dataset.sidebarCollapsed==='false'&&document.querySelector(${JSON.stringify(selector)})?.clientHeight>0`);
    assert.deepEqual((await geometry()).selected,[label],'Detail return restores same tab on expansion');
    await checkScroll(selector,scroll);
    if(label==='AI 助手')assert.equal(await evaluate(`document.querySelector('[aria-label="向助手提问"]').value`),'收起侧栏时保留的分析草稿','Collapsed detail navigation preserves AI draft');
    measurements.push({tab:label,scroll,expanded,collapsed,restored});
  }
  const writes=requests.filter(r=>!['GET','HEAD','OPTIONS'].includes(r.method)&&/\/api\//.test(r.url));
  assert.deepEqual(writes,[],'No business writes');assert.deepEqual(errors,[],'No uncaught browser error');
  const screenshot=await send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync('/tmp/evoalpha-sidebar-collapse.png',Buffer.from(screenshot.data,'base64'));
  console.log(JSON.stringify({date,measurements,writes,errors},null,2));
}finally{ws.close();}
