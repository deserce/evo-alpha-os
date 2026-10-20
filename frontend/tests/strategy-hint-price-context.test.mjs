import assert from 'node:assert/strict';
import test from 'node:test';
import { buildStructureHintPriceContext } from '../src/components/stock/strategyHintPriceContext.ts';
const symbol = '002841', date = '2026-09-30';
const payload = { history_end: date, markers: [{ trade_date: date, marker_type: 'BUY_CONFIRM', price: 45.18, stop_price: 43.27 }], state: { trade_date: date, status: 'HOLDING', entry_date: date, entry_price: 45.18, stop_price: 43.27 } };
test('exact chart C confirmation uses its own prices and date', () => {
 const context = buildStructureHintPriceContext(payload, symbol, date);
 assert.equal(context.items[0].symbol, symbol);
 assert.equal(context.items[0].signals.length, 1);
 assert.equal(context.items[0].signals[0].triggerPrice, 45.18);
 assert.equal(context.items[0].signals[0].confirmedDate, date);
});
test('missing, stale, minute, invalid stock response yields no overlay', () => {
 for (const p of [undefined, {...payload, history_end:'2026-09-29'}, {...payload, state:{...payload.state, trade_date:'2026-09-29'}}, {...payload, period:'5min'}, {...payload,summary:{status:'insufficient_history'}}]) assert.equal(buildStructureHintPriceContext(p, symbol, date), undefined);
 assert.equal(buildStructureHintPriceContext(payload, 'bad', date), undefined);
});
test('held confirmation keeps original date; observing and invalid prices cannot invent entry', () => {
 const holding = buildStructureHintPriceContext({...payload, markers:[], state:{...payload.state, entry_date:'2026-09-28'}}, symbol, date);
 assert.equal(holding.items[0].signals[0].confirmedDate, '2026-09-28');
 for (const status of ['WATCHING', 'UNKNOWN']) assert.equal(buildStructureHintPriceContext({...payload, markers:[], state:{...payload.state,status}},symbol,date),undefined);
 assert.equal(buildStructureHintPriceContext({...payload,markers:[{...payload.markers[0],price:NaN}],state:{...payload.state,entry_price:Infinity}},symbol,date),undefined);
});
