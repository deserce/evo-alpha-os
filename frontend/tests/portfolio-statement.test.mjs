import test from 'node:test';
import assert from 'node:assert/strict';
import { buildStatement } from '../src/lib/portfolio-statement.ts';
const row = (id, date, type, extra = {}) => ({ id, trade_date: date, business_type: type, symbol: '600001', name: '测试股', quantity: 100, price_micros: 10000000, gross_amount_cents: 100000, cash_delta_cents: type === 'BUY' ? -100100 : 99800, fee_cents: 100, tax_cents: type === 'SELL' ? 100 : 0, reversal_of: null, note: null, ...extra });
test('year/month filters and monthly totals span calendar boundaries', () => {
 const rows = [row(1, '2025-12-31', 'BUY'), row(2, '2026-01-01', 'SELL'), row(3, '2026-02-01', 'BUY')];
 assert.deepEqual(buildStatement(rows).years, ['2026','2025']);
 const result = buildStatement(rows, '2026', '01');
 assert.equal(result.rows.length, 1); assert.equal(result.months[0].sellAmount, 100000); assert.equal(result.stocks[0].latestDate, '2026-01-01');
});
test('reversed trades excluded while originals and reversals remain visible', () => {
 const result = buildStatement([row(1, '2026-01-01', 'BUY'), row(2, '2026-02-01', 'REVERSAL', { reversal_of: 1 }), row(3, '2026-01-02', 'BUY', { is_reversed: true })], '2026', '01');
 assert.equal(result.rows.length, 2); assert.ok(result.rows.every(item => item.is_reversed)); assert.equal(result.stocks.length, 0); assert.equal(result.months[0].trades, 0);
});
test('empty input yields empty sections', () => { const result = buildStatement([]); assert.deepEqual(result.rows, []); assert.deepEqual(result.stocks, []); assert.deepEqual(result.months, []); });
test('funding excluded from trade statistics and net cash uses actual cash deltas', () => {
 const result = buildStatement([row(1, '2026-01-01', 'BUY'), row(2, '2026-01-02', 'SELL'), row(3, '2026-01-03', 'DEPOSIT', { cash_delta_cents: 999999 })]);
 assert.equal(result.rows.length, 3); assert.equal(result.stocks[0].trades, 2); assert.equal(result.stocks[0].netCash, -300); assert.equal(result.stocks[0].fees, 300); assert.equal(result.stocks[0].buyQuantity, 100);
});
test('input bounded at 10000 records', () => { const result = buildStatement(Array.from({length:10001}, (_, index) => row(index, '2026-01-01', 'BUY'))); assert.equal(result.rows.length,10000); assert.equal(result.truncated,true); });
