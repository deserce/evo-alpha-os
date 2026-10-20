import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/components/stock/WatchRuleScannerPanel.tsx', import.meta.url),
  'utf8',
);
const workspaceSource = readFileSync(
  new URL('../src/components/stock/StockWorkspace.tsx', import.meta.url),
  'utf8',
);

test('规则页解释路径 A/B 、建设性整理和失效含义', () => {
  assert.match(source, /路径 A · 强势回调/);
  assert.match(source, /路径 B · 领先修复/);
  assert.match(source, /建设性整理/);
  assert.match(source, /失效不等于自动卖出/);
  assert.match(source, /收盘价跌破本轮 setup 的失效价/);
});

test('确认设置区分两条路径共享方法和路径 B 专属方法', () => {
  assert.match(source, /两条路径共享确认（任一满足即可）/);
  assert.match(source, /路径 B 专属确认/);
  assert.match(source, /建设性整理只用于路径 B/);
  assert.match(source, /反转多因子最少共振数/);
});

test('路径 A 可以显式启用 MA10 趋势支撑', () => {
  assert.match(source, /trend_include_ma10_support/);
  assert.match(source, /允许 MA10 趋势支撑/);
  assert.match(source, /MA10\/MA20\/MA50/);
});

test('行动箱也会标识路径 A/B', () => {
  assert.match(workspaceSource, /setupPathCode/);
  assert.match(workspaceSource, /路径 \{pathCode\}/);
});

test('行动箱说明当前状态与首次提醒的口径', () => {
  assert.match(workspaceSource, /所选日期状态，首次转折才提醒/);
  assert.match(workspaceSource, /历史遍历/);
  assert.match(workspaceSource, /冷却中/);
});
