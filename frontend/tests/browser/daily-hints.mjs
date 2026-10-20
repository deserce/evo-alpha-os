import assert from 'node:assert/strict';
import fs from 'node:fs';

const targets = await (await fetch('http://127.0.0.1:9341/json/list')).json();
const target = targets.find((item) => item.type === 'page');
assert.ok(target, 'CDP 9341 必须有浏览器页面');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve) => ws.addEventListener('open', resolve, { once: true }));
let seq = 0;
const pending = new Map();
const errors = [];
ws.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) {
    const request = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) request.reject(Error(message.error.message));
    else request.resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') {
    errors.push(message.params.exceptionDetails.text);
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
const evaluate = async (expression) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw Error(result.exceptionDetails.text);
  return result.result.value;
};
const wait = async (expression) => {
  for (let i = 0; i < 200; i++) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error(`Timeout ${expression}`);
};
const clickTab = async (label) => {
  assert.ok(await evaluate(`(()=>{const tab=[...document.querySelectorAll('[role="tab"]')].find(t=>t.textContent.trim()===${JSON.stringify(label)});tab?.click();return Boolean(tab);})()`), `${label} 页签必须存在`);
  await wait(`[...document.querySelectorAll('[role="tab"]')].some(t=>t.textContent.trim()===${JSON.stringify(label)} && t.getAttribute('aria-selected')==='true')`);
};
const changeDate = async (date) => {
  assert.ok(await evaluate(`(()=>{const select=[...document.querySelectorAll('select')].find(s=>s.querySelector('option[value="${date}"]'));if(!select)return false;select.value='${date}';select.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`), '必须使用真实日期选择器');
  await wait(`document.querySelector('[data-daily-hints]')?.textContent.includes('${date}') && !document.querySelector('[data-daily-hints]').textContent.includes('读取中')`);
};

try {
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: 'http://127.0.0.1:3000/a/watchlist?trade_date=2026-09-30&version=sidebar-daily-hints' });
  await wait(`document.querySelector('[data-stock-sidebar]') && [...document.querySelectorAll('[role="tab"]')].some(t=>t.textContent.trim()==='当日提示')`);
  await clickTab('个股资料');
  assert.equal(await evaluate(`[...document.querySelectorAll('[data-daily-hints]')].filter(p=>p.getClientRects().length>0).length`), 0, '默认资料页没有顶部提示组件');
  assert.ok(await evaluate(`[...document.querySelectorAll('[data-daily-hints]')].every(p=>p.closest('[data-stock-sidebar]'))`), '页面顶部不能残留提示组件');
  await clickTab('当日提示');
  await wait(`document.querySelector('[data-daily-hints]') && !document.querySelector('[data-daily-hints]').textContent.includes('读取中')`);
  await changeDate('2026-09-30');
  const result = await evaluate(`(()=>{const p=document.querySelector('[data-daily-hints]');const cards=[...p.querySelectorAll('[data-hint-symbol]')];const grid=cards[0]?.parentElement;return {insideSidebar:Boolean(p.closest('[data-stock-sidebar]')),codes:cards.map(c=>c.dataset.hintSymbol),cards:cards.length,columns:grid?getComputedStyle(grid).gridTemplateColumns.split(' ').length:0,summary:p.textContent};})()`);
  assert.ok(result.insideSidebar, '当日提示必须位于右侧栏');
  assert.ok(result.cards > 0, '样本日期必须有真实提示卡片');
  assert.equal(new Set(result.codes).size, result.cards, '同股必须只有一张卡片');
  assert.equal(result.columns, 1, '提示卡片必须单列展示');
  const layout = await evaluate(`(()=>{const p=document.querySelector('[data-daily-hints]');const scroll=p.closest('[data-sidebar-scroll]')||p;const c=document.querySelector('[data-workspace-chart]');return {overflow:getComputedStyle(scroll).overflowY,height:scroll.clientHeight,chart:c?.getBoundingClientRect().height};})()`);
  assert.ok(['auto', 'scroll'].includes(layout.overflow), '右侧提示必须独立滚动');
  assert.ok(layout.height > 0 && layout.chart > 200, '提示和图表必须有可用高度');
  await evaluate(`document.querySelector('[data-hint-symbol]').click()`);
  await wait(`document.querySelector('[data-stock-sidebar] [role="tab"][aria-selected="true"]')?.textContent.trim()==='当日提示'`);
  const selectedCode = result.codes[0];
  await wait(`document.querySelector('[data-selected-symbol]')?.getAttribute('data-selected-symbol')==='${selectedCode}'`);
  const before = await evaluate(`document.querySelector('[data-workspace-chart]').getBoundingClientRect().height`);
  assert.ok(await evaluate(`(()=>{const d=[...document.querySelectorAll('[data-daily-hints] details')].find(d=>d.querySelector('summary')?.textContent.includes('历史提醒'));if(!d)return false;d.open=true;return true;})()`), '历史提醒入口必须保留');
  assert.ok(await evaluate(`(()=>{const button=[...document.querySelectorAll('[data-daily-hints] button')].find(b=>b.textContent.trim()==='展开处理 ▾');button?.click();return Boolean(button);})()`), '必须实际展开历史行动箱处理卡片');
  await wait(`[...document.querySelectorAll('[data-daily-hints] button')].some(b=>b.textContent.trim()==='收起 ▴')`);
  await new Promise((resolve) => setTimeout(resolve, 250));
  const expandedLayout = await evaluate(`(()=>{const scroll=document.querySelector('[data-sidebar-scroll]');const sidebar=document.querySelector('[data-stock-sidebar]');return {scrollHeight:scroll.scrollHeight,clientHeight:scroll.clientHeight,scrollWidth:scroll.scrollWidth,clientWidth:scroll.clientWidth,sidebarWidth:sidebar.scrollWidth,sidebarClientWidth:sidebar.clientWidth};})()`);
  assert.ok(expandedLayout.scrollHeight > expandedLayout.clientHeight, '展开历史处理后内容必须在侧栏内部滚动');
  assert.ok(expandedLayout.scrollWidth <= expandedLayout.clientWidth + 1, '侧栏滚动区域不能横向溢出');
  assert.ok(expandedLayout.sidebarWidth <= expandedLayout.sidebarClientWidth + 1, '侧栏不能横向溢出');
  const after = await evaluate(`document.querySelector('[data-workspace-chart]').getBoundingClientRect().height`);
  assert.ok(Math.abs(before - after) <= 1, '展开历史提醒不能压缩图表高度');
  await clickTab('个股资料');
  assert.equal(await evaluate(`[...document.querySelectorAll('[data-daily-hints]')].filter(p=>p.getClientRects().length>0).length`), 0, '资料页隐藏提示');
  await wait(`['业绩', 'F10'].every(label=>[...document.querySelectorAll('[data-stock-sidebar] button')].some(button=>button.textContent.trim()===label && button.getClientRects().length>0))`);
  await clickTab('AI 助手');
  await wait(`document.querySelector('[aria-label="当日提示解读"]')`);
  const shared = await evaluate(`document.querySelector('[aria-label="当日提示解读"]').textContent`);
  assert.ok(shared.includes('共用汇总'), 'AI 必须共享当日提示汇总');
  await clickTab('当日提示');
  await changeDate('2026-09-29');
  const previous = await evaluate(`document.querySelector('[data-daily-hints]').textContent`);
  assert.ok(!previous.includes('2026-09-30'), '切换日期不能遗留上一日提示');
  await changeDate('2026-09-30');
  assert.deepEqual(errors, [], '浏览器不能有未捕获异常');
  const screenshot = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('/tmp/evoalpha-daily-hints-sidebar.png', Buffer.from(screenshot.data, 'base64'));
  console.log(JSON.stringify({ cards: result.cards, codes: result.codes, layout, expandedLayout, shared, dateIsolation: true, errors }, null, 2));
} finally {
  ws.close();
}
