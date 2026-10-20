import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildLowBuyStatusCards,
  lowBuyEventStatusLabel,
  lowBuyStateLabel,
} from '../src/components/stock/stockLowBuyPresentation.ts';

test('正式跟踪与历史回放同状态同 setup 时合并展示', () => {
  const cards = buildLowBuyStatusCards({
    consistency: 'MATCHED',
    tracking_snapshot: {
      watch_state: 'IN_ZONE',
      setup_id: '600584:2026-07-16',
      current_price: 85.49,
      zone_low: 79.709,
      zone_high: 108.177,
      invalidation_price: 78.651,
      reason: '等待低吸确认',
      rule_version: 23,
      factors: { setup_path: 'REPAIR_REVERSAL' },
    },
    scan_snapshot: {
      watch_state: 'IN_ZONE',
      setup_id: '600584:2026-07-16',
      current_price: 85.49,
      rule_version: 23,
      factors: {
        setup_path: 'REPAIR_REVERSAL',
        repair_setup_score: 69,
        repair_confidence: 'MEDIUM',
      },
    },
  });

  assert.equal(cards.length, 1);
  assert.equal(cards[0].source, 'matched');
  assert.equal(cards[0].sourceLabel, '正式跟踪 / 规则回放');
  assert.equal(cards[0].stateLabel, '进入回调区');
  assert.equal(cards[0].pathCode, 'B');
  assert.equal(cards[0].zoneLow, 79.709);
  assert.equal(cards[0].factors.repair_setup_score, 69);
});

test('正式跟踪与历史回放分歧时保留两份状态', () => {
  const cards = buildLowBuyStatusCards({
    consistency: 'DIVERGED',
    tracking_snapshot: {
      watch_state: 'IN_ZONE',
      setup_id: '600584:2026-07-17',
      factors: { setup_path: 'REPAIR_REVERSAL' },
    },
    scan_snapshot: {
      watch_state: 'TRIGGERED',
      setup_id: '600584:2026-07-16',
      factors: { setup_path: 'REPAIR_REVERSAL' },
    },
  });

  assert.deepEqual(cards.map((card) => [card.source, card.state, card.stateLabel]), [
    ['tracking', 'IN_ZONE', '进入回调区'],
    ['scan', 'TRIGGERED', '确认触发'],
  ]);
});

test('空数据和未知字段安全降级', () => {
  assert.deepEqual(buildLowBuyStatusCards({ consistency: 'NO_DATA' }), []);
  assert.equal(lowBuyStateLabel('BROKEN_STATE'), '未知状态');
  assert.equal(lowBuyEventStatusLabel('UNREAD'), '待处理');
  assert.equal(lowBuyEventStatusLabel('ACKNOWLEDGED'), '已确认');
  assert.equal(lowBuyEventStatusLabel(undefined), '无提醒');
});
