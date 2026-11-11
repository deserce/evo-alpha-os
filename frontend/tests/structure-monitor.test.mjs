import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import * as structureMonitor from '../src/lib/structureMonitor.ts';

const { monitorSummary, isStructureMonitorStale, describeStructureReason } = structureMonitor;

const componentSource = (name) => readFileSync(new URL(
  `../src/components/stock/${name}.tsx`, import.meta.url), 'utf8');

test('共享提示隐藏过期监控及同日存档，不把旧信号当作当前结果', () => {
  const source = componentSource('DailyHintsPanel');
  assert.match(source, /const snapshotStale = isStructureMonitorStale\(data\)/);
  assert.match(source, /const monitorCurrent = [\s\S]*?!snapshotStale/);
  assert.equal((source.match(/if \(monitorCurrent\)/g) || []).length, 2);
  assert.match(source, /if \(daily\?\.date === date && !snapshotStale\)/);
});

test('提示和C面板共用中文原因，不直接展示内部阻断码', () => {
  for (const name of ['DailyHintsPanel', 'StructureMonitorPanel']) {
    const source = componentSource(name);
    assert.match(source, /describeStructureReason\(row\.reason\)/);
    assert.doesNotMatch(source, /reason: row\.reason\s*[,}]|\{row\.reason\}<\/span>/);
  }
});

test('C面板过期时不展示旧事件、选股结论或旧价位', () => {
  const source = componentSource('StructureMonitorPanel');
  assert.match(source, /const stale = isStructureMonitorStale\(data\)/);
  assert.match(source, /const selectedRow = !stale/);
  assert.match(source, /const selectedEvent = !stale/);
  assert.match(source, /!stale && data\?\.events\.map/);
  assert.match(source, /!stale && data\?\.rows\.map/);
});

function monitorPayload(overrides = {}) {
  return {
    trade_date: '2026-10-23',
    version: 'test',
    run: { status: 'COMPLETED', updated_at: '2026-10-23T16:00:00' },
    counts: { OBSERVING: 2, HOLDING: 1, BLOCKED: 0 },
    rows: [],
    events: [],
    ...overrides,
  };
}

test('持有状态与当日买卖转折分别统计', () => {
  const summary = monitorSummary({run:{status:'OK'},counts:{HOLDING:3, OBSERVING:2}, events:[
    {event_type:'BUY_CONFIRM'}, {event_type:'EXIT'}, {event_type:'RISK_WARNING'},
  ]}, false, false);
  assert.equal(summary, '观察 2 · 模拟持有 3 · 买入 1 · 卖出 1 · 阻断 0');
});
test('未运行日期明确显示未运行，已有持有不等于当天买入', () => {
  assert.equal(monitorSummary({run:null}, false, false), '所选日期未运行');
  assert.match(monitorSummary({run:{status:'OK'},counts:{HOLDING:1},events:[]}, false, false), /买入 0 · 卖出 0/);
});
test('读取失败不能显示为零信号', () => {
  assert.equal(monitorSummary(undefined, false, true), '读取失败');
  assert.equal(monitorSummary(undefined, true, false), '读取中…');
});

test('行情或质量更新后显示待重新校验，不再展示旧计数', () => {
  for (const reason of ['quality_changed', 'price_data_changed', null]) {
    const data = monitorPayload({ freshness: { status: 'STALE', reason } });
    assert.equal(isStructureMonitorStale(data), true);
    assert.equal(monitorSummary(data, false, false), '行情数据已更新，C结果待重新校验');
  }
});

test('当前结果与缺少 freshness 的旧 payload 保持原摘要', () => {
  for (const overrides of [
    {},
    { freshness: { status: 'CURRENT', reason: null } },
    { freshness: { status: 'CURRENT', reason: 'quality_changed' } },
  ]) {
    const data = monitorPayload(overrides);
    assert.equal(isStructureMonitorStale(data), false);
    assert.equal(monitorSummary(data, false, false),
      '观察 2 · 模拟持有 1 · 买入 0 · 卖出 0 · 阻断 0');
  }
});

test('过期判断只看顶层 freshness，不推断或改写原始运行状态', () => {
  for (const status of ['COMPLETED', 'BLOCKED']) {
    for (const freshnessStatus of ['CURRENT', 'STALE']) {
      const data = monitorPayload({
        run: { status, updated_at: '2026-10-23T16:00:00' },
        counts: { BLOCKED: 1 },
        freshness: { status: freshnessStatus, reason: 'price_data_changed' },
      });
      const original = structuredClone(data);
      Object.freeze(data.run);
      Object.freeze(data.freshness);
      Object.freeze(data);
      assert.equal(isStructureMonitorStale(data), freshnessStatus === 'STALE');
      const summary = monitorSummary(data, false, false);
      if (freshnessStatus === 'STALE') {
        assert.equal(summary, '行情数据已更新，C结果待重新校验');
      } else {
        assert.match(summary, /阻断 1$/);
      }
      assert.deepEqual(data, original);
    }
  }
});

test('缺失、空值或未知 freshness 不会被误判为过期', () => {
  for (const data of [
    undefined, null, monitorPayload(),
    monitorPayload({ freshness: undefined }),
    monitorPayload({ freshness: null }),
    monitorPayload({ freshness: {} }),
    monitorPayload({ freshness: { status: 'UNKNOWN', reason: 'quality_changed' } }),
    monitorPayload({ run: { status: 'STALE' } }),
  ]) {
    assert.equal(isStructureMonitorStale(data), false);
  }
});

test('过期结果仍优先显示读取失败或读取中，不误报恢复', () => {
  const data = monitorPayload({ freshness: { status: 'STALE', reason: 'quality_changed' } });
  assert.equal(monitorSummary(data, false, true), '读取失败');
  assert.equal(monitorSummary(data, true, true), '读取失败');
  assert.equal(monitorSummary(data, true, false), '读取中…');
});

test('缺少运行记录时仍识别显式过期，旧接口缺失则显示未运行', () => {
  const data = monitorPayload({
    run: null, freshness: { status: 'STALE', reason: 'price_data_changed' },
  });
  assert.equal(monitorSummary(data, false, false), '行情数据已更新，C结果待重新校验');
  assert.equal(monitorSummary(monitorPayload({ run: null }), false, false), '所选日期未运行');
  assert.equal(monitorSummary(undefined, false, false), '所选日期未运行');
});

test('各市场日K质量结果缺失与质量检查未通过分别说明', () => {
  for (const [market, label] of [
    ['SH', '沪市'], ['SZ', '深市'], ['BSE', '北交所'], ['BJ', '北交所'], ['ALL', '全市场'],
  ]) {
    assert.equal(describeStructureReason(`stock_daily_quality_missing:${market}`),
      `${label}日K质量检查结果缺失`);
    assert.equal(describeStructureReason(`stock_daily_quality_failed:${market}`),
      `${label}日K质量检查未通过`);
  }
});

test('未知市场或内部质量详情不泄露，且仍区分缺失与失败', () => {
  for (const market of ['UNKNOWN', '*', '__proto__', 'SH:internal_detail', '内部错误']) {
    const label = market.startsWith('SH:') ? '沪市' : '';
    assert.equal(describeStructureReason(`stock_daily_quality_missing:${market}`),
      `${label}日K质量检查结果缺失`);
    assert.equal(describeStructureReason(`stock_daily_quality_failed:${market}`),
      `${label}日K质量检查未通过`);
  }
});

test('已知行情与执行边界原因均转换为中文', () => {
  for (const [reason, expected] of [
    ['target_daily_bar_missing', '所选交易日缺少日K行情'],
    ['insufficient_history', '历史日K数据不足'],
    ['history_bar_limit_exceeded', '历史日K数量超出本次校验上限'],
    ['monitor_time_budget_exceeded', '本次校验耗时超出上限，请稍后重试'],
    ['symbol_removed_from_all_pools', '该股票已移出所有股票池'],
  ]) {
    assert.equal(describeStructureReason(reason), expected);
  }
});

test('非法行情原因隐藏异常详情，含中文的内部异常也不原样展示', () => {
  for (const reason of [
    'invalid_ohlcv', 'invalid_ohlcv: close must be positive',
    'invalid_ohlcv: 成交量异常 at /private/internal.py:123',
  ]) {
    assert.equal(describeStructureReason(reason), '日K价格或成交量数据异常');
  }
});

test('中文原因原样保留，包括常用指标与原有空白', () => {
  for (const reason of ['等待当日可信日线数据', '等待 MA20 支撑确认', 'MACD 尚未转强', '  等待结构确认  ']) {
    assert.equal(describeStructureReason(reason), reason);
  }
});

test('机器码周围空白不影响已知原因识别', () => {
  assert.equal(describeStructureReason('  target_daily_bar_missing\n'), '所选交易日缺少日K行情');
  assert.equal(describeStructureReason(' stock_daily_quality_missing:SH '), '沪市日K质量检查结果缺失');
});

test('未知机器码、英文详情及空值均使用安全中文兜底', () => {
  for (const reason of [
    undefined, null, '', ' \n ', 123, {},
    'unknown_internal_reason', 'UNKNOWN', 'unexpected internal failure',
    'unknown_internal_reason: 内部错误', 'unknown_internal_reason 内部错误',
    'target_daily_bar_missing_extra', 'invalid_ohlcv_extra: 内部错误',
    '<script>alert(1)</script>',
  ]) {
    const description = describeStructureReason(reason);
    assert.equal(description, '暂无可展示的原因说明');
    assert.doesNotMatch(description, /[a-z_<>]/i);
  }
});
