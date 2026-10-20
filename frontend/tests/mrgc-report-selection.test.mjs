import assert from 'node:assert/strict';
import test from 'node:test';
import { selectReportStocks, reportLinkedStocks, parseReportLink, reportContextPool, parseStockDetailDate } from '../src/lib/mrgcReportSelection.ts';

const stocks = [
  { symbol: '600001', status: 'HOLDING', sector_name: '半导体', events: [] },
  { symbol: '600002', status: 'HOLDING', sector_name: '半导体', events: [{ event_type: 'BUY_CONFIRM' }] },
  { symbol: '600003', status: 'WATCHING', sector_name: '医药', events: [{ event_type: 'INVALIDATED' }, { event_type: 'RISK_WARNING' }] },
];
test('持续模拟持有不会被筛成今日新增买点', () => {
  assert.deepEqual(selectReportStocks(stocks, 'buy').map(s => s.symbol), ['600002']);
  assert.equal(selectReportStocks(stocks, 'holding').length, 1);
});
test('风险和失效同时发生的股票只出现一次，行业筛选可交叉使用', () => {
  assert.equal(selectReportStocks(stocks, 'risk').length, 1);
  assert.equal(selectReportStocks(stocks, 'risk', '半导体').length, 0);
  assert.equal(stocks.length, 3);
});
test('空筛选安全返回，未知筛选明确拒绝', () => {
  assert.deepEqual(selectReportStocks([], 'buy'), []);
  assert.throws(() => selectReportStocks(stocks, 'bad'), /未知/);
});
test('历史日报事实原样联动，缺少RPS不冒充0', () => {
  const links = reportLinkedStocks([{symbol: '600001', name:'测试', sector_name:'半导体',
    price:12.5, pct_chg:-1, rps20:null, last_hit_date:'2026-09-30', hit_count:3}]);
  assert.equal(links[0].close,12.5);
  assert.equal(links[0].rps_20,null);
  assert.match(links[0].note,/2026-09-30.*3 次/);
});

test('邮件链接严格保留股票和历史日期，拒绝畸形日期和代码', () => {
  assert.deepEqual(parseReportLink('?pool_id=smart_mrgc_20d&symbol=600001&trade_date=2026-09-30'), {date:'2026-09-30',symbol:'600001'});
  assert.equal(parseReportLink('?pool_id=smart_mrgc_20d&symbol=600001&trade_date=2026-02-30'),null);
  assert.equal(parseReportLink('?pool_id=smart_mrgc_20d&symbol=../bad&trade_date=2026-09-30'),null);
  assert.equal(parseReportLink('?symbol=600001&trade_date=2026-09-30'),null);
});


test('统一买卖机会优先、阻断排除、显式空列表不回退',()=>{
 const rows=[{...stocks[0],opportunities:[{direction:'buy',level:'chan_only'}]},
 {...stocks[1],opportunities:[]}, {...stocks[2],opportunities:[{direction:'sell',level:'resonance'}]}];
 assert.deepEqual(selectReportStocks(rows,'buy').map(s=>s.symbol),['600001']);
 assert.deepEqual(selectReportStocks(rows,'exit').map(s=>s.symbol),['600003']);
 assert.equal(selectReportStocks(rows,'risk').length,1);
 assert.equal(selectReportStocks(rows,'holding').length,1);
 assert.equal(selectReportStocks([{...rows[0],data_status:'BLOCKED'}],'buy').length,0);
 assert.equal(selectReportStocks([{...stocks[1],status:'BLOCKED'}],'buy').length,0);
});
test('分组方向行业交叉、旧MRGC兼容、重复组不算交集',()=>{
 const rows=[{...stocks[0],group_ids:['company','watchlist']},{...stocks[1],group_ids:['mrgc','company']},
 {...stocks[2],group_ids:['watchlist','watchlist']}];
 assert.equal(selectReportStocks(rows,'all','','overlap').length,2);
 assert.equal(selectReportStocks(rows,'buy','半导体','company').length,1);
 assert.equal(selectReportStocks(stocks,'all','','mrgc').length,3);
 assert.equal(selectReportStocks(stocks,'all','','company').length,0);
 assert.throws(()=>selectReportStocks(rows,'all','','bad'),/未知日报分组/);
});
test('非MRGC成员联动备注显示实际分组与未命中',()=>{
 const links=reportLinkedStocks([{symbol:'600001',name:'测试',sector_name:'机械',price:1,pct_chg:0,
 rps20:null,last_hit_date:'',hit_count:0,group_names:['公司持仓']}]);
 assert.equal(links[0].note,'公司持仓 · MRGC 未命中');
});


test('联合日报联动使用真实标签且独立于MRGC组',()=>{
 const c={date:'2026-09-30',label:'公司与自选交集 · 买点',stocks:[{},{}]};
 assert.deepEqual(reportContextPool(c,c.date),{pool_id:'report_linked',name:'日报联动 · 公司与自选交集 · 买点',stock_count:2,is_report:true,monitor_enabled:0});
 assert.equal(reportContextPool(c,'2026-09-29'),null);
 assert.equal(reportContextPool(null,c.date),null);
});
test('个股邮件历史日期严格校验且不依赖MRGC池',()=>{
 assert.equal(parseStockDetailDate('?trade_date=2026-09-30'),'2026-09-30');
 for(const q of ['?trade_date=2026-02-30','?trade_date=2026-9-3','?trade_date=2022-12-31','']) assert.equal(parseStockDetailDate(q),null);
});
