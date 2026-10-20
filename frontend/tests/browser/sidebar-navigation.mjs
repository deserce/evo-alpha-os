// Production UI regression. Start isolated Chrome with --remote-debugging-port=9342.
// Only initial load uses Page.navigate; all subsequent transitions use real Next links.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const base = process.env.APP_BASE_URL || 'http://127.0.0.1:3000';
const date = process.env.TEST_TRADE_DATE || '2026-09-30';
const archiveResponse = await fetch(`${base}/api/v1/watch/mrgc-report?trade_date=${date}`);
assert.ok(archiveResponse.ok, 'Sample date report must be readable');
const archive = (await archiveResponse.json()).data;
assert.ok(archive?.archive?.complete && archive?.report, 'Require existing completed archive; never generate in regression');
const targets = await (await fetch('http://127.0.0.1:9342/json/list')).json();
const target = targets.find(item => item.type === 'page');
assert.ok(target, 'CDP 9342 requires an isolated browser page');
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
const goDetail = async () => {
  const code = await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`);
  assert.ok(await evaluate(`(()=>{const link=[...document.querySelectorAll('a')].find(a=>a.textContent.trim()==='个股详情' && a.getClientRects().length);link?.click();return Boolean(link);})()`), 'Use actual Next Link to detail');
  await wait(`location.pathname==='/a/stock/${code}' && ${sidebar} && ['个股资料','AI 助手','当日提示'].every(label=>[...${sidebar}.querySelectorAll('[role="tab"]')].some(t=>t.textContent.trim()===label))`);
  return code;
};
const goBack = async () => {
  await click('返回', 'document');
  await wait(`location.pathname==='/a/watchlist' && document.querySelector('[data-selected-symbol]') && ${sidebar}`);
};
const hintsState = () => evaluate(`(()=>{const p=document.querySelector('[data-daily-hints]');return {summary:p.querySelector('summary')?.textContent,codes:[...p.querySelectorAll('[data-hint-symbol]')].map(e=>e.dataset.hintSymbol),details:[...p.querySelectorAll('details')].map(e=>({name:e.querySelector('summary')?.textContent,open:e.open})),historyExpanded:[...p.querySelectorAll('button')].some(b=>b.textContent.trim()==='收起 ▴')};})()`);
const infoState = () => evaluate(`(()=>{const p=${sidebar};return {buttons:[...p.querySelectorAll('button[aria-pressed="true"]')].map(b=>b.textContent.trim()),f10Visible:[...p.querySelectorAll('button')].find(b=>b.textContent.trim()==='F10')?.className,f10Sections:[...p.querySelectorAll('[data-sidebar-f10-section]')].map(d=>({category:d.dataset.sidebarF10Section,open:d.open}))};})()`);
const aiState = () => evaluate(`(()=>{const p=document.querySelector('[data-daily-assistant]');return {group:p.querySelector('[aria-label="日报分组范围"] [aria-pressed="true"]')?.textContent,view:[...p.querySelectorAll('button')].filter(b=>['问助手','完整日报','邮件预览'].includes(b.textContent.trim())).map(b=>({label:b.textContent.trim(),className:b.className})),filter:[...p.querySelectorAll('button')].filter(b=>b.querySelector('span')?.textContent==='买入关注').map(b=>b.className),answer:p.querySelector('h3')?.textContent,question:p.querySelector('input[aria-label="向助手提问"]')?.value,codes:[...p.querySelectorAll('article button')].map(b=>b.textContent)};})()`);
try {
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `${base}/a/watchlist?version=sidebar-navigation` });
  await wait(`${sidebar} && document.querySelector('[data-selected-symbol]')`);
  assert.ok(await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>e.querySelector('option[value="${date}"]'));if(!s)return false;s.value='${date}';s.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`), 'Use real trade date selector');
  await tab('当日提示');
  await wait(`document.querySelector('[data-daily-hints]')?.textContent.includes('${date}') && !document.querySelector('[data-daily-hints]').textContent.includes('读取中') && document.querySelector('[data-hint-symbol]')`);
  // Use the existing selected pool. Its name and complete scope must survive navigation.
  await evaluate(`(()=>{for(const d of document.querySelectorAll('[data-daily-hints] details')){if(!d.open)d.querySelector('summary')?.click();}})()`);
  const legacyExpand = await evaluate(`(()=>{const b=[...document.querySelectorAll('[data-daily-hints] button')].find(b=>b.textContent.trim()==='展开处理 ▾');b?.click();return Boolean(b);})()`);
  if (legacyExpand) await wait(`[...document.querySelectorAll('[data-daily-hints] button')].some(b=>b.textContent.trim()==='收起 ▴')`);
  const hintsBefore = await hintsState(); const hintsScroll = await setScroll('[data-sidebar-scroll]');
  assert.equal(new Set(hintsBefore.codes).size, hintsBefore.codes.length, 'Hints are deduplicated');
  await goDetail(); await tab('当日提示');
  await wait(`document.querySelectorAll('[data-hint-symbol]').length===${hintsBefore.codes.length}`);
  assert.deepEqual(await hintsState(), hintsBefore, 'Detail preserves complete pool scope and expanded hints');
  await checkScroll('[data-sidebar-scroll]', hintsScroll);
  await goBack();
  await wait(`${sidebar}.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim()==='当日提示'`);
  assert.deepEqual(await hintsState(), hintsBefore, 'Returning preserves hint state');
  await checkScroll('[data-sidebar-scroll]', hintsScroll);

  await tab('个股资料');
  await wait(`[...${sidebar}.querySelectorAll('button')].some(b=>b.textContent.trim()==='业绩')`);
  await click('业绩');
  await wait(`[...${sidebar}.querySelectorAll('button')].some(b=>b.textContent.trim()==='营收')`);
  await click('营收');
  await wait(`[...${sidebar}.querySelectorAll('button[aria-pressed="true"]')].some(b=>b.textContent.trim()==='营收')`);
  const metricBefore = await infoState();
  await goDetail(); await wait(`[...${sidebar}.querySelectorAll('button[aria-pressed="true"]')].some(b=>b.textContent.trim()==='营收')`);
  assert.deepEqual(await infoState(), metricBefore, 'Financial metric survives detail navigation');
  await goBack(); await click('F10');
  await wait(`document.querySelector('[data-sidebar-info-scroll]') && document.querySelector('[data-sidebar-f10-section]')`);
  const f10Category = await evaluate(`(()=>{const sections=[...document.querySelectorAll('[data-sidebar-f10-section]')];const d=sections.sort((a,b)=>b.textContent.length-a.textContent.length)[0];if(!d.open)d.querySelector('summary').click();return d.dataset.sidebarF10Section;})()`);
  await wait(`[...document.querySelectorAll('[data-sidebar-f10-section]')].some(d=>d.dataset.sidebarF10Section===${JSON.stringify(f10Category)} && d.open)`);
  await wait(`document.querySelector('[data-sidebar-info-scroll]').scrollHeight>document.querySelector('[data-sidebar-info-scroll]').clientHeight`);
  const infoBefore = await infoState(); const infoScroll = await setScroll('[data-sidebar-info-scroll]');
  assert.ok(infoScroll>0, 'Expanded F10 must exercise a nonzero scroll position');
  await goDetail();
  await wait(`[...document.querySelectorAll('[data-sidebar-f10-section]')].some(d=>d.dataset.sidebarF10Section===${JSON.stringify(f10Category)} && d.open)`);
  assert.deepEqual(await infoState(), infoBefore, 'F10 selection and expanded section survive detail navigation');
  await checkScroll('[data-sidebar-info-scroll]', infoScroll);
  await goBack();
  await wait(`[...document.querySelectorAll('[data-sidebar-f10-section]')].some(d=>d.dataset.sidebarF10Section===${JSON.stringify(f10Category)} && d.open)`);
  assert.deepEqual(await infoState(), infoBefore, 'F10 selection and expanded section survive return');
  await checkScroll('[data-sidebar-info-scroll]', infoScroll);

  // Existing archive only. Never click regenerate or invoke write endpoints.
  await tab('AI 助手');
  await wait(`document.querySelector('[data-daily-assistant]')?.textContent.includes('完整存档')`);
  await click('今天有哪些机会？');
  await wait(`document.querySelector('[data-daily-assistant] h3')`);
  await evaluate(`(()=>{const e=document.querySelector('[aria-label="向助手提问"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'保留当前机会对比草稿');e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await wait(`document.querySelector('[aria-label="向助手提问"]').value==='保留当前机会对比草稿'`);
  const chatBefore = await aiState(); const chatScroll = await setScroll('[data-sidebar-ai-scroll]');
  await goDetail(); assert.deepEqual(await aiState(), chatBefore, 'AI answer and draft survive detail navigation'); await checkScroll('[data-sidebar-ai-scroll]', chatScroll);
  await goBack(); assert.deepEqual(await aiState(), chatBefore, 'AI answer and draft survive return');
  await click('完整日报');
  await wait(`document.querySelector('[aria-label="日报分组范围"]')`);
  await click('自选股', `document.querySelector('[aria-label="日报分组范围"]')`, true);
  await click('买入关注', 'document.querySelector("[data-daily-assistant]")', true);
  const reportBefore = await aiState(); const reportScroll = await setScroll('[data-sidebar-ai-scroll]');
  await goDetail(); assert.deepEqual(await aiState(), reportBefore, 'Report view, group, filter survive detail'); await checkScroll('[data-sidebar-ai-scroll]', reportScroll);
  await goBack(); assert.deepEqual(await aiState(), reportBefore, 'Report view, group, filter survive return'); await checkScroll('[data-sidebar-ai-scroll]', reportScroll);
  await click('问助手');
  assert.equal(await evaluate(`document.querySelector('[aria-label="向助手提问"]').value`), '保留当前机会对比草稿', 'Hidden report view retains draft');

  const sharedRequests = requests.filter(r=>r.method==='GET' && /(?:mrgc-report|snapshots)/.test(r.url));
  for (let index=0;index<sharedRequests.length;index++) {
    const current=sharedRequests[index];
    const staleWindow = current.url.includes('snapshots') ? 30 : 60;
    assert.ok(!sharedRequests.slice(0,index).some(r=>r.url===current.url && current.timestamp-r.timestamp<staleWindow), `Shared query duplicated within ${staleWindow}s: ${current.url}`);
  }
  const writes=requests.filter(r=>!['GET','HEAD','OPTIONS'].includes(r.method) && /\/api\//.test(r.url));
  assert.deepEqual(writes, [], 'Navigation regression must not write production data');
  assert.deepEqual(errors, [], 'No uncaught browser exceptions');
  const screenshot=await send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync('/tmp/evoalpha-sidebar-navigation.png',Buffer.from(screenshot.data,'base64'));
  console.log(JSON.stringify({date,hints:hintsBefore.codes.length,scroll:{hints:hintsScroll,info:infoScroll,chat:chatScroll,report:reportScroll},sharedRequests,writes,errors},null,2));
} finally { ws.close(); }
