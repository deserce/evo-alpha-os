import test from 'node:test';
import assert from 'node:assert/strict';
import { buildEquitySeries } from '../src/lib/portfolio-chart.ts';
const row = (date, overrides = {}) => ({snapshot_date:date, cash_cents:100000, total_equity_cents:100000, market_value_cents:0, net_external_flow_cents:0, pnl_cents:0, return_ppb:0, price_status:'COMPLETE', performance_status:'COMPLETE', return_base_date:null, ...overrides});
test('初始本金与后续入金不成为盈利，逐日收益连乘', () => {
 const data = buildEquitySeries([row('2026-08-06',{performance_status:'INITIAL',net_external_flow_cents:2000000}),row('2026-08-07',{return_base_date:'2026-08-06',pnl_cents:1000,return_ppb:10000000,net_external_flow_cents:2000000}),row('2026-08-10',{return_base_date:'2026-08-07',pnl_cents:-1000,return_ppb:-10000000})], 'all');
 assert.equal(data[0].pnl,0); assert.equal(data[1].pnl,10); assert.equal(data[2].pnl,0); assert.ok(Math.abs(data[2].return + 0.01)<1e-8);
});
test('缺失行情后累计链不静默重启', () => {
 const data = buildEquitySeries([row('2026-08-06',{performance_status:'INITIAL'}),row('2026-08-07',{price_status:'PARTIAL',total_equity_cents:null}),row('2026-08-10',{return_base_date:'2026-08-07'})], 'all');
 assert.equal(data[1].equity,null); assert.equal(data[2].pnl,null); assert.equal(data[2].return,null);
});
test('近1月使用前一交易日锚点，计入区间首日收益', () => {
 const data = buildEquitySeries([row('2026-08-20',{performance_status:'INITIAL'}),row('2026-08-24',{return_base_date:'2026-08-20',pnl_cents:200,return_ppb:2000000}),row('2026-09-22',{return_base_date:'2026-08-24',pnl_cents:300,return_ppb:3000000})], '1m');
 assert.equal(data[0].date,'2026-08-24'); assert.equal(data[0].pnl,2); assert.equal(data[1].pnl,5);
});
test('漏交易日通过return_base_date检测，零权益合法，空数据安全', () => {
 assert.deepEqual(buildEquitySeries([], 'all'),[]);
 const data = buildEquitySeries([row('2026-08-06',{performance_status:'INITIAL',total_equity_cents:0}),row('2026-08-10',{return_base_date:'2026-08-07'})], 'all');
 assert.equal(data[0].equity,0); assert.equal(data[1].return,null);
});

test('月末区间按上月最后一天截断，不溢出到当月', () => {
 const data=buildEquitySeries([row('2026-02-27',{performance_status:'INITIAL'}),row('2026-02-28',{return_base_date:'2026-02-27'}),row('2026-03-31',{return_base_date:'2026-02-28'})],'1m');
 assert.equal(data[0].date,'2026-02-28');
});
