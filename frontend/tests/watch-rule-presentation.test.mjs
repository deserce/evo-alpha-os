import assert from 'node:assert/strict';
import test from 'node:test';

import {
  describeWatchFactors,
  setupPathCode,
  setupPathLabel,
  validateRepairRuleConfig,
} from '../src/components/stock/watchRulePresentation.ts';

test('扫描结果明确区分强势回调和领先修复路径', () => {
  assert.equal(setupPathCode('TREND_PULLBACK'), 'A');
  assert.equal(setupPathCode('REPAIR_REVERSAL'), 'B');
  assert.equal(setupPathLabel('TREND_PULLBACK'), '路径 A · 强势回调');
  assert.equal(setupPathLabel('REPAIR_REVERSAL'), '路径 B · 领先修复');
  assert.equal(setupPathLabel(undefined), '未入区');
});

test('领先修复因子摘要展示得分、置信度和因果变化', () => {
  const labels = describeWatchFactors({
    setup_path: 'REPAIR_REVERSAL',
    repair_setup_score: 67,
    repair_confidence: 'MEDIUM',
    market_strength_delta: 10,
    industry_strength_delta: -3,
    repair_confirmation_count: 3,
  });

  assert.deepEqual(labels, [
    '路径 B · 领先修复',
    '修复分 67 · 中置信',
    '市场 +10.0',
    '行业 -3.0',
    '确认共振 3/4',
  ]);
});

test('修复路径开启时拦截空状态、回撤倒挂和无效确认数', () => {
  assert.deepEqual(validateRepairRuleConfig({
    repair_path_enabled: false,
  }), []);
  assert.deepEqual(validateRepairRuleConfig({
    repair_path_enabled: true,
    repair_market_states: [],
    repair_industry_states: ['I2'],
    pullback_max_pct: 15,
    repair_pullback_max_pct: 10,
    repair_setup_score_min: 60,
    repair_trigger_score_min: 50,
    repair_confirmation_min_count: 1,
  }), [
    '修复路径至少选择一个市场和行业状态',
    '修复路径最大回撤不能小于强势回调上限',
    '修复路径触发分不能低于入区分',
    '修复确认共振数必须在 2-4 之间',
  ]);
});
