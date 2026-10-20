// Read-only production regression for grouped daily hints. Isolated CDP 9343.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const base = process.env.APP_BASE_URL || 'http://127.0.0.1:3000';
const date = process.env.TEST_TRADE_DATE || '2026-09-30';
const archiveResponse = await fetch(`${base}/api/v1/watch/mrgc-report?trade_date=${date}`);
assert.ok(archiveResponse.ok, 'Sample archive readable');
const archive = (await archiveResponse.json()).data;
assert.ok(archive?.archive?.complete && archive?.report, 'Require complete existing archive; do not generate');
const targets = await (await fetch('http://127.0.0.1:9343/json/list')).json();
const target = targets.find(item => item.type === 'page');
assert.ok(target, 'CDP 9343 requires an isolated browser page');
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
const state = () => evaluate(`(()=>{const p=document.querySelector('[data-daily-hints]');return {summary:p.firstElementChild.textContent,filter:p.querySelector('[data-hint-filter][aria-pressed="true"]')?.dataset.hintFilter,codes:[...p.querySelectorAll('[data-hint-symbol]')].map(e=>e.dataset.hintSymbol),expanded:[...p.querySelectorAll('details')].map(d=>({label:d.querySelector('summary')?.textContent,open:d.open}))};})()`);
try {
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width:1600,height:1000,deviceScaleFactor:1,mobile:false });
  await send('Page.navigate',{url:`${base}/a/watchlist?version=daily-hints-content`});
  await wait(`${sidebar} && document.querySelector('[data-selected-symbol]')`);
  assert.ok(await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>e.querySelector('option[value="${date}"]'));if(!s)return false;s.value='${date}';s.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`), 'Use real date select');
  await tab('当日提示');
  await wait(`document.querySelector('[data-daily-hints]')?.textContent.includes('${date}') && !document.querySelector('[data-daily-hints]').textContent.includes('读取中') && document.querySelector('[data-hint-symbol]')`);
  const defaults = await evaluate(`(()=>{const p=document.querySelector('[data-daily-hints]');return [...p.querySelectorAll('details')].map(d=>({label:d.querySelector('summary')?.textContent,open:d.open}));})()`);
  await evaluate(`document.querySelector('[data-hint-filter="all"]').click()`);
  await wait(`document.querySelector('[data-hint-filter="all"]').getAttribute('aria-pressed')==='true'`);
  await evaluate(`(()=>{for(const d of [...document.querySelectorAll('[data-hint-symbol] details')].slice(0,3))if(!d.open)d.querySelector('summary').click();})()`);
  const before=await state();
  assert.equal(new Set(before.codes).size,before.codes.length,'Each stock has one hint card');
  const content=await evaluate(`(()=>{const p=document.querySelector('[data-daily-hints]');return [...p.querySelectorAll('[data-hint-symbol]')].map(a=>({symbol:a.dataset.hintSymbol,direction:a.dataset.hintDirection,text:a.textContent,priceLines:[...a.querySelectorAll('[data-hint-prices]')].map(e=>e.textContent),group:a.closest('[data-hint-group]')?.dataset.hintGroup}));})()`);
  assert.ok(content.length>0,'Real sample has hint cards');
  const rank={buy:0,risk:1,sell:1,wait:2,blocked:3};
  for(let i=1;i<content.length;i++) assert.ok(rank[content[i].direction]>=rank[content[i-1].direction],'Buy cards precede risk/sell, then waiting and blocked');
  const groupedText=await evaluate(`document.querySelector('[data-daily-hints]').textContent`);
  if(content.some(c=>c.direction==='risk')) assert.ok(groupedText.includes('风险提示'),'Risk group label');
  if(content.some(c=>c.direction==='sell')) assert.ok(groupedText.includes('卖点关注'),'Sell group label');
  if(content.some(c=>c.direction==='buy')) assert.ok(groupedText.includes('买点关注'),'Buy group label');
  const candidate=content.find(c=>/缠[买卖]候选/.test(c.text));
  assert.ok(candidate,'Actual sample contains labeled Chan candidate');
  assert.ok(candidate.text.includes('ZIG')&&candidate.text.includes('回绘'),'Chan candidate explicitly states repaint limitation');
  assert.ok(!candidate.text.includes('缠论确认'),'Candidate is not represented as confirmation');
  const cCard=content.find(c=>/C [买卖]点/.test(c.text));
  if(cCard) assert.ok(cCard.text.includes('确认日'),'C buy/sell labels include confirmation date');
  for(const card of content){
    assert.match(card.symbol,/^\d{6}$/);
    assert.ok(card.text.includes('下一步：'),'Every card explains next step');
    assert.ok(card.priceLines.length>0,'Every card includes labeled source price fields');
    assert.ok(!/NaN|Infinity|undefined|null/.test(card.priceLines.join(' ')),'Invalid numeric values are not displayed');
  }
  assert.ok(content.some(card=>card.priceLines.some(line=>/(?:现价|信号价|拐点价|失效线)\s+\d+\.\d{2}/.test(line))),'Real source prices are rendered');
  // Verify actual archived price rather than merely the presence of numeric text.
  const matched=content.find(card=>archive.report.stocks.some(stock=>stock.symbol===card.symbol && Number.isFinite(stock.price) && card.priceLines.some(line=>line.includes(`现价 ${stock.price.toFixed(2)}`))));
  assert.ok(matched,'At least one current price matches the real saved report stock');
  await tab('AI 助手');
  await wait(`document.querySelector('[data-daily-assistant]')?.textContent.includes('完整存档')`);
  await click('问助手');
  await wait(`document.querySelector('[aria-label="向助手提问"]')`);
  await evaluate(`(()=>{const e=document.querySelector('[aria-label="向助手提问"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'保留当日提示分析草稿');e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await wait(`document.querySelector('[aria-label="向助手提问"]').value==='保留当日提示分析草稿'`);
  await tab('当日提示');
  const poolOptions=await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>[...e.options].some(o=>o.textContent.includes('只')));return s?[...s.options].filter(o=>o.value&&!o.value.startsWith('__')).map(o=>({value:o.value,label:o.textContent})):[];})()`);
  let outside;
  for(const pool of poolOptions){
    const response=await fetch(`${base}/api/v1/stock/pools/${encodeURIComponent(pool.value)}/stocks?trade_date=${date}`);
    assert.ok(response.ok,'Pool members are read-only and readable');
    const payload=(await response.json()).data;
    assert.ok(Array.isArray(payload?.stocks),'Actual poolStocks API supplies members');
    const membership=new Set(payload.stocks.map(stock=>stock.symbol));
    const symbol=before.codes.find(code=>!membership.has(code));
    if(symbol){outside={pool,symbol};break;}
  }
  assert.ok(outside,'Full-universe sample has a card outside an actual left pool');
  await evaluate(`(()=>{const s=[...document.querySelectorAll('select')].find(e=>[...e.options].some(o=>o.textContent.includes('只')));s.value=${JSON.stringify(outside.pool.value)};s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await wait(`[...document.querySelectorAll('select')].some(s=>s.value===${JSON.stringify(outside.pool.value)}) && document.querySelector('[data-hint-symbol="${outside.symbol}"]')`);
  const code=outside.symbol;
  assert.ok(await evaluate(`(()=>{const a=document.querySelector('[data-hint-symbol="${code}"]');const b=[...a.querySelectorAll('button')].find(e=>e.textContent.trim()==='看图');b?.click();return Boolean(b);})()`),'Real outside-pool look-at-chart action');
  await wait(`document.querySelector('[data-selected-symbol]')?.dataset.selectedSymbol==='${code}'`);
  await new Promise(resolve=>setTimeout(resolve,500));
  assert.equal(await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`),code,'Outside-pool chart selection remains stable');
  assert.ok(await evaluate(`(()=>{const a=[...document.querySelectorAll('[data-hint-symbol]')].find(e=>e.dataset.hintSymbol==='${code}');const b=[...a.querySelectorAll('button')].find(e=>e.textContent.trim()==='AI 解读');b?.click();return Boolean(b);})()`),'Actual hint AI explanation action exists');
  await wait(`${sidebar}.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim()==='AI 助手' && document.querySelector('[data-selected-symbol]')?.dataset.selectedSymbol==='${code}'`);
  await new Promise(resolve=>setTimeout(resolve,500));
  assert.equal(await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`),code,'Outside-pool AI selection remains stable');
  assert.equal(await evaluate(`document.querySelector('[aria-label="向助手提问"]').value`),'保留当日提示分析草稿','Hint explanation preserves question draft');
  await wait(`document.querySelector('[data-hints-return-bar]')?.textContent.includes('${code}')`);
  await tab('当日提示');
  assert.deepEqual(await state(),before,'AI round trip preserves group scope, cards and expanded state');
  assert.ok(await evaluate(`(()=>{const link=[...document.querySelectorAll('a')].find(a=>a.textContent.trim()==='个股详情'&&a.getClientRects().length);link?.click();return Boolean(link);})()`),'Use actual Next Link to detail');
  await wait(`location.pathname==='/a/stock/${code}' && document.querySelector('[data-selected-symbol]')?.dataset.selectedSymbol==='${code}' && document.querySelector('[data-hint-filter="all"]')?.getAttribute('aria-pressed')==='true'`);
  assert.deepEqual(await state(),before,'Outside-pool detail preserves full scope and active filter');
  await click('返回','document');
  await wait(`location.pathname==='/a/watchlist' && document.querySelector('[data-selected-symbol]')?.dataset.selectedSymbol==='${code}'`);
  await new Promise(resolve=>setTimeout(resolve,500));
  assert.equal(await evaluate(`document.querySelector('[data-selected-symbol]').dataset.selectedSymbol`),code,'Outside-pool selection survives detail return');
  assert.deepEqual(await state(),before,'Detail return preserves full scope/filter/state');
  const writes=requests.filter(r=>!['GET','HEAD','OPTIONS'].includes(r.method)&&/\/api\//.test(r.url));
  assert.deepEqual(writes,[],'No business write requests');
  assert.deepEqual(errors,[],'No uncaught browser exception');
  const screenshot=await send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync('/tmp/evoalpha-daily-hints-content.png',Buffer.from(screenshot.data,'base64'));
  console.log(JSON.stringify({date,cards:content.length,realPriceMatched:matched.symbol,code,defaults,writes,errors},null,2));
} finally { ws.close(); }
