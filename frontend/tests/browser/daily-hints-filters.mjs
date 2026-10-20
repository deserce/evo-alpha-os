// Read-only production regression for grouped daily hints. Isolated CDP 9344.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const base = process.env.APP_BASE_URL || 'http://127.0.0.1:3000';
const date = process.env.TEST_TRADE_DATE || '2026-09-30';
const archiveResponse = await fetch(`${base}/api/v1/watch/mrgc-report?trade_date=${date}`);
assert.ok(archiveResponse.ok, 'Sample archive readable');
const archive = (await archiveResponse.json()).data;
assert.ok(archive?.archive?.complete && archive?.report, 'Require complete existing archive; do not generate');
const targets = await (await fetch('http://127.0.0.1:9344/json/list')).json();
const target = targets.find(item => item.type === 'page');
assert.ok(target, 'CDP 9344 requires an isolated browser page');
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
const hints='document.querySelector("[data-daily-hints]")';
const filter = async key => {
  assert.ok(await evaluate(`(()=>{const b=${hints}.querySelector('[data-hint-filter="${key}"]');b?.click();return Boolean(b);})()`), `Real filter button ${key}`);
  await wait(`${hints}.querySelector('[data-hint-filter="${key}"]')?.getAttribute('aria-pressed')==='true'`);
};
const snapshot=()=>evaluate(`(()=>{const p=${hints};return {counts:[...p.querySelectorAll('[data-hint-filter]')].map(b=>({key:b.dataset.hintFilter,text:b.textContent.replace('✓ ','')})),cards:[...p.querySelectorAll('[data-hint-symbol]')].map(a=>({symbol:a.dataset.hintSymbol,direction:a.dataset.hintDirection,ordinal:Number(a.dataset.hintOrdinal),labels:a.querySelector(':scope > div:not([data-hint-header])')?.querySelectorAll('span.border').length,evidence:[...a.querySelectorAll('details')].map(d=>({label:d.querySelector('summary')?.textContent,open:d.open,bodyVisible:[...d.children].filter(e=>e.tagName!=='SUMMARY').some(e=>e.checkVisibility())})),visible:a.getClientRects().length>0 && !a.closest('details:not([open])')}))};})()`);
try {
  await send('Runtime.enable');await send('Page.enable');await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:`${base}/a/watchlist?version=hints-filter-scope`});
  await wait(`${sidebar} && document.querySelector('[data-selected-symbol]')`);
  assert.ok(await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>e.querySelector('option[value="${date}"]'));if(!s)return false;s.value='${date}';s.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`),'Actual date select');
  await tab('当日提示');
  await wait(`${hints}?.textContent.includes('${date}') && !${hints}.textContent.includes('读取中') && !${hints}.textContent.includes('尚未完成') && ${hints}.querySelector('[data-hint-filter="all"]')`);
  await filter('all');
  const baseline=await snapshot();
  assert.ok(baseline.cards.length>0,'Sample date has actual hints');
  assert.equal(await evaluate(`${hints}.querySelectorAll('[data-hint-group],h3').length`),0,'Results use one flat list without repeated category sections');
  assert.equal(await evaluate(`${hints}.querySelectorAll('[data-hint-type]').length`),baseline.cards.length,'Every card has its own type label');
  const checkCompact = (cards, label) => {
    assert.deepEqual(cards.map(card=>card.ordinal),cards.map((_card,index)=>index+1),`${label} ordinals are continuous 1..N`);
    for(const card of cards){
      assert.ok(card.labels<=4,`${label} ${card.symbol} shows type and no more than 3 source tags`);
      assert.ok(card.evidence.some(d=>d.label==='更多依据'),`${label} retains complete evidence entry`);
      assert.ok(card.evidence.every(d=>!d.open&&!d.bodyVisible),`${label} full evidence is collapsed and hidden by default`);
    }
  };
  checkCompact(baseline.cards,'all');
  assert.equal(new Set(baseline.cards.map(c=>c.symbol)).size,baseline.cards.length,'Full universe deduplicates stock cards');
  const directions=baseline.cards.map(c=>c.direction);
  const firstBuy=directions.indexOf('buy');
  const firstRiskSell=directions.findIndex(d=>['risk','sell'].includes(d));
  if(firstBuy>=0&&firstRiskSell>=0) assert.ok(firstBuy<firstRiskSell,'All view puts buy opportunities before risk/sell');
  for(const key of ['buy','sell','risk','wait','blocked']){
    await filter(key);
    const current=await snapshot();
    const expected=baseline.cards.filter(c=>c.direction===key);
    checkCompact(current.cards,key);
    assert.deepEqual(current.cards.map(c=>c.symbol),expected.map(c=>c.symbol),`${key} filters actual matching cards`);
    assert.ok(current.cards.every(c=>c.direction===key&&c.visible),`${key} cards visible without secondary expansion`);
    assert.deepEqual(current.counts,baseline.counts,'Filtering does not change universe counts');
    assert.equal(await evaluate(`${hints}.querySelectorAll('details[aria-label="提示列表"]').length`),0,'No summary accordion can intercept filter clicks');
  }
  await filter('all');
  const poolOptions=await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>[...e.options].some(o=>o.textContent.includes('只')));return s?[...s.options].filter(o=>o.value&&!o.value.startsWith('__')).map(o=>({value:o.value,label:o.textContent})):[];})()`);
  assert.ok(poolOptions.length>=2,'At least two actual Watchlist pools for scope regression');
  for(const option of poolOptions.slice(0,3)){
    assert.ok(await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>[...e.options].some(o=>o.textContent.includes('只')));s.value=${JSON.stringify(option.value)};s.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`),'Change actual left pool select');
    await wait(`[...document.querySelectorAll('select')].some(s=>s.value===${JSON.stringify(option.value)}) && ${hints} && !${hints}.textContent.includes('读取中') && !${hints}.textContent.includes('尚未完成')`);
    await new Promise(resolve=>setTimeout(resolve,400));
    const after=await snapshot();
    checkCompact(after.cards,`pool ${option.label}`);
    assert.deepEqual(after.counts,baseline.counts,`Full-Watchlist counts unaffected by left pool ${option.label}`);
    assert.deepEqual(after.cards.map(c=>c.symbol),baseline.cards.map(c=>c.symbol),`Full-Watchlist membership unaffected by left pool ${option.label}`);
    assert.ok(await evaluate(`${hints}.querySelector('[data-hint-filter="all"]').getAttribute('aria-pressed')==='true'`),'Pool switch retains active filter');
  }
  assert.ok(baseline.cards.length>=2,'Selection regression requires two actual cards');
  const selectedCode=baseline.cards[1].symbol;
  assert.ok(await evaluate(`(()=>{const a=${hints}.querySelector('[data-hint-symbol="${selectedCode}"]');const b=[...a.querySelectorAll('[data-hint-header] button')].find(e=>e.textContent.trim()==='看图');b?.click();return Boolean(b);})()`),'Click second stock actual look-at-chart button');
  await wait(`document.querySelector('[data-selected-symbol]')?.dataset.selectedSymbol==='${selectedCode}' && ${hints}.querySelector('[data-hint-symbol="${selectedCode}"]')?.dataset.hintSelected==='true'`);
  await new Promise(resolve=>setTimeout(resolve,500));
  assert.equal(await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`),selectedCode,'Selected stock stays current');
  assert.deepEqual(await evaluate(`[...${hints}.querySelectorAll('[data-hint-selected="true"]')].map(a=>a.dataset.hintSymbol)`),[selectedCode],'Exactly one hint card highlighted for global selected stock');
  const headers=await evaluate(`(()=>{return [...${hints}.querySelectorAll('[data-hint-header]')].map(h=>{const r=h.getBoundingClientRect();return {width:r.width,scrollWidth:h.scrollWidth,clientWidth:h.clientWidth,buttons:[...h.querySelectorAll('button')].map(b=>{const q=b.getBoundingClientRect();return {label:b.getAttribute('title')||b.textContent.trim(),center:q.y+q.height/2,left:q.left,right:q.right};}),left:r.left,right:r.right};});})()`);
  for(const header of headers){
    assert.equal(header.buttons.length,3,'Header includes name, chart and AI buttons');
    assert.ok(Math.max(...header.buttons.map(b=>b.center))-Math.min(...header.buttons.map(b=>b.center))<=2,'All three header actions aligned on one row');
    assert.ok(header.scrollWidth<=header.clientWidth+1,'Header has no horizontal overflow');
    assert.ok(header.buttons.every(b=>b.left>=header.left-1&&b.right<=header.right+1),'Header actions stay inside card');
  }
  async function returnToHint(category) {
    await filter(category);
    await evaluate(`(()=>{const a=${hints}.querySelector('[data-hint-symbol="${selectedCode}"]');a.querySelector('details').open=true;const e=document.querySelector('[data-sidebar-scroll]');e.scrollTop=220;e.dispatchEvent(new Event('scroll'));})()`);
    await new Promise(r=>setTimeout(r,100));
    const top=await evaluate(`document.querySelector('[data-sidebar-scroll]').scrollTop`);
    assert.ok(top>0,'Return sample starts at a nonzero scroll position');
    await evaluate(`(()=>{const a=${hints}.querySelector('[data-hint-symbol="${selectedCode}"]');[...a.querySelectorAll('button')].find(b=>b.textContent.trim()==='AI 解读').click();})()`);
    await wait(`document.querySelector('[data-hints-return-bar]')?.getClientRects().length`);
    assert.ok(await evaluate(`document.querySelector('[data-hints-return-bar]').textContent.includes('${selectedCode}')`),'Return bar identifies interpreted stock');
    await wait(`document.querySelector('[data-hint-explanation]')`);
    const explanation=await evaluate(`document.querySelector('[data-hint-explanation]').textContent`);
    assert.ok(explanation.includes(selectedCode)&&explanation.includes('RPS20')&&explanation.includes('结构'),'AI opens detailed current-stock analysis');
    assert.ok(!/买入关注 14|Watchlist全部|现价 \d|信号价 \d/.test(explanation),'AI explanation does not repeat global totals or price rows');
    await evaluate(`[...document.querySelector('[data-daily-assistant]').querySelectorAll('button')].find(b=>b.textContent.trim()==='日报背景').click()`);
    await wait(`document.querySelector('[data-report-background]')`);
    assert.equal(await evaluate(`document.querySelector('[data-report-background]').querySelectorAll('[data-hint-symbol]').length`),0,'Report background has no second opportunity list');
    await evaluate(`[...document.querySelector('[data-daily-assistant]').querySelectorAll('button')].find(b=>b.textContent.trim()==='邮件预览').click()`);
    await wait(`document.querySelector('iframe[title="统一日报邮件预览，不发送邮件"]')`);
    assert.ok(await evaluate(`document.querySelector('iframe').getAttribute('srcdoc').length>100`),'Email preview retains existing archive HTML');
    await evaluate(`[...document.querySelector('[data-daily-assistant]').querySelectorAll('button')].find(b=>b.textContent.trim()==='问助手').click()`);
    await evaluate(`document.querySelector('[data-hints-return-bar] button').click()`);
    await wait(`${hints}.querySelector('[data-hint-filter="${category}"]')?.getAttribute('aria-pressed')==='true' && document.querySelector('[data-sidebar-scroll]')?.getClientRects().length`);
    await new Promise(r=>setTimeout(r,300));
    assert.ok(Math.abs(await evaluate(`document.querySelector('[data-sidebar-scroll]').scrollTop`)-top)<2,'Return restores original scroll position');
    assert.ok(await evaluate(`${hints}.querySelector('[data-hint-symbol="${selectedCode}"] details').open`),'Return retains expanded evidence');
    assert.deepEqual(await evaluate(`[...${hints}.querySelectorAll('[data-hint-selected="true"]')].map(a=>a.dataset.hintSymbol)`),[selectedCode],'Return retains selected stock');
  }
  async function answerLinkRoundTrip() {
    await evaluate(`(()=>{const a=${hints}.querySelector('[data-hint-symbol="${selectedCode}"]');[...a.querySelectorAll('button')].find(b=>b.textContent.trim()==='AI 解读').click();})()`);
    await wait(`document.querySelector('[data-hints-return-bar]')?.getClientRects().length`);
    await evaluate(`[...document.querySelector('[data-daily-assistant]').querySelectorAll('button')].find(b=>b.textContent.trim()==='今天有哪些机会？').click()`);
    await wait(`document.querySelector('[data-answer-hints]')`);
    const codes=await evaluate(`[...document.querySelectorAll('[data-answer-hint-symbol]')].map(e=>e.dataset.answerHintSymbol)`);
    assert.deepEqual(codes,baseline.cards.filter(c=>c.direction==='buy').map(c=>c.symbol).slice(0,20),'Answer uses same ordered Watchlist buy results');
    await evaluate(`(()=>{const e=document.querySelector('[data-sidebar-ai-scroll]');e.scrollTop=240;e.dispatchEvent(new Event('scroll'));})()`);
    await new Promise(r=>setTimeout(r,100));
    const answerTop=await evaluate(`document.querySelector('[data-sidebar-ai-scroll]').scrollTop`);
    const code=codes[1];
    await evaluate(`document.querySelector('[data-answer-hint-symbol="${code}"]').click()`);
    await wait(`${hints}.querySelector('[data-hint-symbol="${code}"]')?.dataset.hintSelected==='true' && document.querySelector('[data-answer-return]')?.getClientRects().length`);
    assert.equal(await evaluate(`${hints}.querySelector('[data-hint-filter="buy"]').getAttribute('aria-pressed')`),'true','Answer link selects matching hints category');
    await evaluate(`document.querySelector('[data-answer-return] button').click()`);
    await wait(`document.querySelector('[data-answer-hints]')?.getClientRects().length`);
    assert.deepEqual(await evaluate(`[...document.querySelectorAll('[data-answer-hint-symbol]')].map(e=>e.dataset.answerHintSymbol)`),codes,'Return preserves original answer');
    await new Promise(r=>setTimeout(r,300));
    assert.ok(Math.abs(await evaluate(`document.querySelector('[data-sidebar-ai-scroll]').scrollTop`)-answerTop)<2,'Return restores answer scroll position');
    await evaluate(`document.querySelector('[data-hints-return-bar] button').click()`);
    await wait(`${hints}?.getClientRects().length`);
  }
  await returnToHint('all');
  await returnToHint('buy');
  await answerLinkRoundTrip();
  await evaluate(`(()=>{[...document.querySelectorAll('a')].find(a=>a.textContent.trim()==='个股详情'&&a.getClientRects().length).click();})()`);
  await wait(`location.pathname==='/a/stock/${selectedCode}' && ${hints}?.querySelector('[data-hint-symbol="${selectedCode}"]')`);
  await returnToHint('all');
  await answerLinkRoundTrip();
  await wait(`document.querySelector('[data-hint-chart-symbol="${selectedCode}"] [data-hint-chart-price]')`);
  assert.equal(await evaluate(`document.querySelector('[data-hint-chart-symbol]').dataset.hintChartDate`),date,'Chart prices belong to selected date');
  const chartPrices=await evaluate(`[...document.querySelectorAll('[data-hint-chart-price]')].map(e=>Number(e.dataset.hintChartPrice))`);
  assert.ok(chartPrices.every(p=>Number.isFinite(p)&&p>0)&&chartPrices.length<=12,'Chart price overlay is finite and bounded');
  await evaluate(`(()=>{const c=document.querySelector('[data-workspace-chart]');[...c.querySelectorAll('button')].find(b=>b.textContent.trim()==='周K').click();})()`);
  await wait(`document.querySelectorAll('[data-hint-chart-price]').length===0`);
  await evaluate(`(()=>{const c=document.querySelector('[data-workspace-chart]');[...c.querySelectorAll('button')].find(b=>b.textContent.trim()==='日K').click();})()`);
  await wait(`document.querySelector('[data-hint-chart-price]')`);
  await wait(`document.querySelector('[data-hint-axis-price]')`);
  const axis=await evaluate(`[...document.querySelectorAll('[data-hint-axis-price]')].map(e=>{const r=e.getBoundingClientRect(),p=e.closest('[aria-label="策略关键价位"]').getBoundingClientRect();return {text:e.textContent.trim(),title:e.title,top:r.top,bottom:r.bottom,left:r.left,right:r.right,parentRight:p.right};})`);
  assert.ok(axis.every(e=>/^\d+\.\d{2}$/.test(e.text)&&e.title.includes(e.text)),'Axis shows only numbers and keeps source in hover text');
  assert.ok(axis.every(e=>e.right<=e.parentRight+1),'Price chips stay within chart right edge');
  const ordered=[...axis].sort((a,b)=>a.top-b.top);
  assert.ok(ordered.every((e,i)=>!i||e.top>=ordered[i-1].bottom-1),'Axis price chips do not overlap');
  // Explicit origin routing survives detail-stock replacement and retains sidebar state.
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='返回').click()`);
  await wait(`location.pathname==='/a/watchlist' && document.querySelector('[data-selected-symbol]')`);
  assert.equal(await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`),selectedCode,'Watchlist return retains selected stock');
  await evaluate(`[...document.querySelectorAll('a')].find(a=>a.getAttribute('href')==='/a/strategy').click()`);
  await wait(`document.querySelector('tr[data-symbol]')`);
  const strategyCode=await evaluate(`document.querySelector('tr[data-symbol]').dataset.symbol`);
  await evaluate(`document.querySelector('tr[data-symbol]').click()`);
  await wait(`document.querySelector('[data-workspace-chart]') && document.querySelector('[data-daily-hints]')`);
  assert.ok(await evaluate(`['个股资料','AI 助手','当日提示'].every(t=>[...document.querySelectorAll('[role="tab"]')].some(b=>b.textContent.trim()===t))`),'Strategy uses all three shared sidebar tabs');
  await evaluate(`[...document.querySelectorAll('button[role="tab"]')].find(b=>b.textContent.trim()==='当日提示').click()`);
  await wait(`document.querySelector('[data-daily-hints]')?.getClientRects().length`);
  assert.deepEqual((await snapshot()).counts,baseline.counts,'Strategy hints share the full Watchlist context');
  await evaluate(`document.querySelector('button[aria-label="收起右侧栏"]').click()`);
  await wait(`document.querySelector('button[aria-label="展开右侧栏"]')`);
  await evaluate(`document.querySelector('button[aria-label="展开右侧栏"]').click()`);
  await evaluate(`[...document.querySelectorAll('a')].find(a=>a.textContent.trim()==='个股详情').click()`);
  await wait(`location.pathname==='/a/stock/${strategyCode}' && [...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='返回')`);
  assert.equal(await evaluate(`new URLSearchParams(location.search).get('returnTo')`),'/a/strategy','Strategy origin encoded in detail URL');
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='返回').click()`);
  await wait(`location.pathname==='/a/strategy' && document.querySelector('[data-workspace-chart]')`);
  assert.ok(await evaluate(`[...document.querySelectorAll('a')].some(a=>a.textContent.trim()==='个股详情'&&a.href.includes('/a/stock/${strategyCode}'))`),'Strategy return restores selection and open panel');
  const writes=requests.filter(r=>!['GET','HEAD','OPTIONS'].includes(r.method)&&/\/api\//.test(r.url));
  assert.deepEqual(writes,[],'Regression has no business writes');assert.deepEqual(errors,[],'No browser runtime error');
  const screenshot=await send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync('/tmp/evoalpha-daily-hints-filters.png',Buffer.from(screenshot.data,'base64'));
  console.log(JSON.stringify({date,cards:baseline.cards.length,counts:baseline.counts,pools:poolOptions.slice(0,3),filters:['all','buy','sell','risk','wait','blocked'],writes,errors},null,2));
}finally{ws.close();}
