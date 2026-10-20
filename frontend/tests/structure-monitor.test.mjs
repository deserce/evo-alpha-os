import assert from 'node:assert/strict';
import test from 'node:test';
import { monitorSummary } from '../src/lib/structureMonitor.ts';

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
