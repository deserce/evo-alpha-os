import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';
import { buildChanOverlay } from '../src/components/stock/stockChartChanOverlay.ts';
const source = fs.readFileSync(new URL('../src/components/stock/stockChartChanHistory.ts', import.meta.url), 'utf8').replace("from './stockChartChanOverlay'", `from '${new URL('../src/components/stock/stockChartChanOverlay.ts', import.meta.url)}'`);
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { buildChanHistory } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const datesFor = n => Array.from({ length: n }, (_, i) => new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10));
const wave = n => Array.from({ length: n }, (_, i) => {
  const p = 100 + Math.sin(i * Math.PI / 9) * (15 + Math.sin(i / 20) * 10) + Math.sin(i / 27) * 15;
  return [p - 0.2, p, p - 1, p + 1];
});
const key = z => `${z.startIndex}:${z.type}:${z.dashedTop || z.dashedBottom ? 'forming' : 'confirmed'}`;
test('逐前缀重算并保留每种结构状态的最后快照', () => {
  const dates = datesFor(180), candles = wave(180), expected = new Map();
  for (let end = 1; end <= dates.length; end++) {
    for (const z of buildChanOverlay(dates.slice(0, end), candles.slice(0, end), 'xueqiu').zones) {
      expected.delete(key(z)); expected.set(key(z), z);
      if (expected.size > 64) expected.delete(expected.keys().next().value);
    }
  }
  const r = buildChanHistory(dates, candles);
  assert.ok(r.zones.length > 0);
  assert.deepEqual(r.zones, [...expected.values()]);
  assert.equal(r.barCount, 180); assert.equal(r.truncated, false);
  assert.ok(r.elapsedMs >= 0);
  assert.equal(new Set(r.zones.map(key)).size, r.zones.length);
});
test('空、单根、单调行情安全返回', () => {
  assert.deepEqual(buildChanHistory([], []).zones, []);
  assert.equal(buildChanHistory(datesFor(1), [[100, 100, 99, 101]]).barCount, 1);
  const bars = datesFor(80).map((_, i) => [100+i, 100+i, 99+i, 101+i]);
  assert.deepEqual(buildChanHistory(datesFor(80), bars).zones, []);
});
test('坏行情、错误版本和绝对输入超限拒绝', () => {
  for (const [dates, candles, variant] of [
    [datesFor(2), [[100, 100, 99, 101]], 'xueqiu'],
    [datesFor(1), [[100, null, 99, 101]], 'xueqiu'],
    [datesFor(1), [[100, 100, 99, 101]], 'unknown'],
    [datesFor(4097), wave(4097), 'xueqiu'],
  ]) {
    const r = buildChanHistory(dates, candles, variant);
    assert.equal(r.barCount, 0); assert.deepEqual(r.zones, []);
  }
});
test('截断至520根并保留绝对索引，框数不超过64', () => {
  const dates = datesFor(540), candles = wave(540);
  const r = buildChanHistory(dates, candles);
  const tail = buildChanHistory(dates.slice(20), candles.slice(20));
  assert.equal(r.barCount, 520); assert.equal(r.truncated, true);
  assert.ok(r.zones.length <= 64);
  assert.deepEqual(r.zones, tail.zones.map(z => ({ ...z,
    startIndex: z.startIndex + 20, endIndex: z.endIndex + 20 })));
});

test('Worker有界请求协议返回成功和错误，不触发外部访问', async () => {
  const vm = await import('node:vm');
  const worker = fs.readFileSync(new URL('../src/components/stock/stockChartChanHistory.worker.ts', import.meta.url), 'utf8');
  const workerJs = ts.transpileModule(worker, { compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
  } }).outputText.replace(/^import .*;$/gm, '').replace(/^export \{\};$/gm, '');
  const messages = [], scope = { postMessage: message => messages.push(message), onmessage: null };
  vm.runInNewContext(workerJs, { self: scope, buildChanHistory, CHAN_MAX_INPUT_BARS: 4096 });
  scope.onmessage({ data: { id: 1, dates: datesFor(80), candles: wave(80) } });
  assert.equal(messages[0].id, 1); assert.equal(messages[0].status, 'success');
  scope.onmessage({ data: { id: 2, dates: datesFor(4097), candles: wave(4097) } });
  assert.equal(messages[1].status, 'error');
  scope.onmessage({ data: { id: 3, dates: datesFor(1), candles: [[null, 1, 1, 1]] } });
  assert.equal(messages[2].status, 'error');
  scope.onmessage({ data: { id: 4, dates: datesFor(80), candles: wave(80), variant: 'zhihu', auditSignals: true, includeZones: false } });
  assert.equal(messages[3].status, 'success');
  assert.deepEqual(messages[3].result.zones, []);
  scope.onmessage({ data: { id: 5, dates: datesFor(80), candles: wave(80), auditSignals: 'yes' } });
  assert.equal(messages[4].status, 'error');
});

test('风华高科固定起点：9月29日类2买在9月30日撤销，记录真实出现和撤销时点', () => {
  const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/chan-000636-20260930.json', import.meta.url)));
  const n = fixture.dates.indexOf('2026-09-29') + 1;
  const before = buildChanHistory(fixture.dates.slice(0,n), fixture.candles.slice(0,n), 'zhihu', {auditSignals:true,includeZones:false});
  const after = buildChanHistory(fixture.dates, fixture.candles, 'zhihu', {auditSignals:true,includeZones:false});
  const removed = after.revokedSignals.find(p => p.label === '[类 2 买]' && p.revokedAt === '2026-09-30');
  assert.ok(removed);
  assert.ok(!before.revokedSignals.some(p => p.index === removed.index && p.label === removed.label));
  assert.ok(removed.firstSeen <= '2026-09-29');
  assert.equal(after.auditStartDate,fixture.dates[0]);
  assert.equal(after.auditEndDate,'2026-09-30');
  assert.deepEqual(after.zones,[]);
  const extension = buildChanHistory(fixture.dates,fixture.candles,'xueqiu',{auditSignals:true,includeZones:false});
  assert.deepEqual(extension.revokedSignals,after.revokedSignals);
});

test('通用波形：撤销记录不覆盖最终活跃文字，有限输出且默认不审计', () => {
  const dates=datesFor(520), candles=wave(520);
  const result=buildChanHistory(dates,candles,'zhihu',{auditSignals:true,includeZones:false});
  const final=buildChanOverlay(dates,candles,'zhihu').points;
  assert.ok(result.revokedSignalCount>0);
  assert.ok(result.revokedSignals.length<=128);
  for(const r of result.revokedSignals){
    assert.ok(r.firstSeen<=r.revokedAt);
    assert.ok(Number.isFinite(r.price));
    assert.ok(!final.some(p=>p.index===r.index && p.kind===r.kind && p.label===r.label));
  }
  assert.deepEqual(buildChanHistory(dates.slice(0,80),candles.slice(0,80)).revokedSignals,[]);
  assert.deepEqual(buildChanHistory([],[],'zhihu',{auditSignals:true}).revokedSignals,[]);
});

test('审计坏输入不计算，分钟日期完整保留',()=>{
  assert.equal(buildChanHistory(['2026-09-29'],[[null,1,1,1]],'zhihu',{auditSignals:true}).barCount,0);
  const dates=datesFor(100).map(d=>`${d} 15:00`);
  const result=buildChanHistory(dates,wave(100),'zhihu',{auditSignals:true,includeZones:false});
  assert.equal(result.auditStartDate,dates[0]);
  for(const r of result.revokedSignals)assert.ok(r.firstSeen.endsWith('15:00')&&r.revokedAt.endsWith('15:00'));
});
