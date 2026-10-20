// Read-only production regression for grouped daily hints. Isolated CDP 9346.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const base = process.env.APP_BASE_URL || 'http://127.0.0.1:3000';
const date = process.env.TEST_TRADE_DATE || '2026-09-30';
const archiveResponse = await fetch(`${base}/api/v1/watch/mrgc-report?trade_date=${date}`);
assert.ok(archiveResponse.ok, 'Sample archive readable');
const archive = (await archiveResponse.json()).data;
assert.ok(archive?.archive?.complete && archive?.report, 'Require complete existing archive; do not generate');
const targets = await (await fetch('http://127.0.0.1:9346/json/list')).json();
const target = targets.find(item => item.type === 'page');
assert.ok(target, 'CDP 9346 requires an isolated browser page');
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
const measure=async(label)=>{
  await tab(label);
  await wait(`${sidebar}.querySelector('[data-sidebar-heading]')?.getClientRects().length>0 && document.querySelector(${JSON.stringify(scrollSelectors[label])})?.clientHeight>0`);
  return evaluate(`(()=>{const p=${sidebar};const r=p.getBoundingClientRect();const ts=[...p.querySelectorAll('[role="tab"]')];const headings=[...p.querySelectorAll('[data-sidebar-heading]')].filter(e=>e.getClientRects().length);const sc=document.querySelector(${JSON.stringify(scrollSelectors[label])});const active=ts.find(e=>e.getAttribute('aria-selected')==='true');const style=getComputedStyle(active);return {tab:${JSON.stringify(label)},x:r.x,width:r.width,selected:ts.filter(e=>e.getAttribute('aria-selected')==='true').map(e=>e.textContent.trim()),activeStyle:{color:style.color,background:style.backgroundColor,border:style.borderColor,font:style.fontWeight},inactive:ts.filter(e=>e!==active).map(e=>({color:getComputedStyle(e).color,background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderColor})),headingHeights:headings.map(e=>e.getBoundingClientRect().height),horizontal:{scroll:p.scrollWidth,client:p.clientWidth,body:document.documentElement.scrollWidth,viewport:innerWidth},scroll:{overflow:getComputedStyle(sc).overflowY,client:sc.clientHeight,height:sc.scrollHeight},chart:document.querySelector('[data-workspace-chart]')?.getBoundingClientRect().height};})()`);
};
const validate=m=>{
  assert.deepEqual(m.selected,[m.tab],'Exactly one selected top tab');
  assert.ok(m.inactive.every(i=>i.background!==m.activeStyle.background&&i.border!==m.activeStyle.border),'Active tab has distinct background and border');
  assert.ok(m.headingHeights.length===1,'One visible panel header');
  assert.ok(m.headingHeights.every(h=>Math.abs(h-48)<=1),`Panel heading must be 48px: ${m.headingHeights}`);
  assert.ok(m.horizontal.scroll<=m.horizontal.client+1,'Sidebar cannot horizontally overflow');
  assert.ok(m.horizontal.body<=m.horizontal.viewport+1,'Page cannot horizontally overflow');
  assert.ok(['auto','scroll'].includes(m.scroll.overflow)&&m.scroll.client>0,'Panel scrolls independently');
  assert.ok(m.chart>200,'Chart keeps usable height');
};
const detail=async()=>{
  const symbol=await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`);
  assert.ok(await evaluate(`(()=>{const a=[...document.querySelectorAll('a')].find(e=>e.textContent.trim()==='个股详情'&&e.getClientRects().length);a?.click();return Boolean(a);})()`),'Actual detail Next Link');
  await wait(`location.pathname==='/a/stock/${symbol}' && ${sidebar}`);
};
const back=async()=>{await click('返回','document');await wait(`location.pathname==='/a/watchlist'&&${sidebar}`);};
try{
  await send('Runtime.enable');await send('Page.enable');await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:`${base}/a/watchlist?version=sidebar-ui`});
  await wait(`${sidebar} && document.querySelector('[data-selected-symbol]')`);
  assert.ok(await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>e.querySelector('option[value="${date}"]'));if(!s)return false;s.value='${date}';s.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`),'Actual date select');
  for(const width of [1600,1920]){
    await send('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    let baseline;
    for(const label of tabs){
      const watch=await measure(label);validate(watch);
      if(!baseline)baseline=watch;
      assert.ok(Math.abs(watch.width-baseline.width)<=1&&Math.abs(watch.x-baseline.x)<=1,'Switching tabs keeps sidebar width and position');
      const scrollSelector=scrollSelectors[label];
      const beforeChart=watch.chart;
      await setScroll(scrollSelector);
      assert.ok(Math.abs((await measure(label)).chart-beforeChart)<=1,'Sidebar scroll cannot alter chart height');
      await detail();
      const stock=await measure(label);validate(stock);
      assert.ok(Math.abs(stock.width-watch.width)<=1,`${width}px ${label} detail and Watchlist share sidebar width`);
      await back();
      const returned=await measure(label);validate(returned);
      assert.ok(Math.abs(returned.width-watch.width)<=1&&Math.abs(returned.x-watch.x)<=1,'Returning retains sidebar geometry');
      measurements.push({viewport:width,watch,stock,returned});
    }
    const screenshot=await send('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(`/tmp/evoalpha-sidebar-ui-${width}.png`,Buffer.from(screenshot.data,'base64'));
  }
  const writes=requests.filter(r=>!['GET','HEAD','OPTIONS'].includes(r.method)&&/\/api\//.test(r.url));
  assert.deepEqual(writes,[],'UI regression performs no business writes');assert.deepEqual(errors,[],'No uncaught browser errors');
  console.log(JSON.stringify({date,measurements,writes,errors},null,2));
}finally{ws.close();}
