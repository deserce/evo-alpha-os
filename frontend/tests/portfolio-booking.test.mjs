import test from 'node:test';
import assert from 'node:assert/strict';
import { rowIssues, validateImages, bookingRequestId } from '../src/lib/portfolio-booking.ts';
const row = { row_id: '1', business_type: 'BUY', trade_date: '2026-08-06', trade_time: '09:31', symbol: '600276', price_micros: 53520000, quantity: 100, fee_cents: 105, amount_cents: null };
test('完整成交及资金流水可预览', () => { assert.deepEqual(rowIssues(row), []); assert.deepEqual(rowIssues({...row, business_type: 'DEPOSIT', amount_cents: 2000000, symbol: null}), []); });
test('不存在日期、缺税费及小数数量不能选入', () => { assert.ok(rowIssues({...row, trade_date: '2026-02-30', fee_cents: null, quantity: 1.2}).length === 3); });
test('图片限制覆盖类型、单张、总量和张数', () => { const file = {size: 5*1024*1024, type:'image/png'}; assert.equal(validateImages([file]), null); assert.ok(validateImages(Array(9).fill(file))); assert.ok(validateImages(Array(7).fill(file))); assert.ok(validateImages([{...file,size:9*1024*1024}])); assert.ok(validateImages([{...file,type:'text/plain'}])); });
test('同一分钟同价分笔不会被行校验静默移除', () => { const rows = [row, {...row, row_id:'2'}]; assert.equal(rows.filter(item => rowIssues(item).length === 0).length, 2); assert.notEqual(bookingRequestId(), bookingRequestId()); });
test('交割净额不一致阻止选择，保留截图金额供更正', () => {
  assert.deepEqual(rowIssues({...row, net_cash_cents: -535305}), []);
  assert.ok(rowIssues({...row, net_cash_cents: -535304}).includes('交割净额与价格、数量及税费不一致'));
  assert.deepEqual(rowIssues({...row, business_type:'SELL', net_cash_cents:535095}), []);
  assert.ok(rowIssues({...row, business_type:'DEPOSIT', amount_cents:2000000, net_cash_cents:1999999}).length > 0);
});
test('价格微元以分四舍五入核验，不更改原始数据', () => {
  const fractional = {...row, price_micros:1000005, quantity:1000, fee_cents:100, net_cash_cents:-100101};
  assert.deepEqual(rowIssues(fractional), []);
  assert.equal(fractional.net_cash_cents,-100101);
});
