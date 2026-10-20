import assert from 'node:assert/strict';
import test from 'node:test';

import {
  resolveStrategyDate,
  strategyDateOptions,
} from '../src/lib/strategyDateSelection.ts';

test('全局明确日期即使没有策略结果也保持原日期', () => {
  const dates = ['2026-07-29', '2026-07-24'];

  assert.equal(resolveStrategyDate('2026-07-28', dates), '2026-07-28');
  assert.deepEqual(strategyDateOptions('2026-07-28', dates), [
    '2026-07-28',
    '2026-07-29',
    '2026-07-24',
  ]);
});

test('没有全局日期时使用最新可用日期', () => {
  const dates = ['2026-07-29', '2026-07-28'];

  assert.equal(resolveStrategyDate('', dates), '2026-07-29');
  assert.deepEqual(strategyDateOptions('', dates), dates);
});

test('已有日期不重复插入选项', () => {
  const dates = ['2026-07-29', '2026-07-28'];

  assert.equal(resolveStrategyDate('2026-07-28', dates), '2026-07-28');
  assert.deepEqual(strategyDateOptions('2026-07-28', dates), dates);
});
