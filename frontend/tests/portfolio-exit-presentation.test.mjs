import assert from 'node:assert/strict';
import test from 'node:test';
import { cExitPresentation } from '../src/lib/portfolio-exit-presentation.ts';

test('C退出展示准确原因及未绑定结构线', () => {
  assert.deepEqual(cExitPresentation('TREND_EXIT_TRIGGERED', JSON.stringify({
    strategy: 'C', reason: '连续两日失守MA10且MACD动能走弱', stop_binding: 'UNBOUND',
  })), {label: 'C 结构退出', reason: '连续两日失守MA10且MACD动能走弱', structureNote: '结构失效线未绑定'});
});

test('预警不会冒充退出，外部文本由React按文本展示', () => {
  assert.deepEqual(cExitPresentation('TREND_EXIT_WATCH', JSON.stringify({
    strategy: 'C', reason: '<script>x</script>', stop_binding: 'BOUND_AT_ENTRY',
  })), {label: 'C 风险预警', reason: '<script>x</script>', structureNote: '结构失效线已绑定（前复权）'});
});

test('旧规则、非趋势事件与损坏载荷安全回退', () => {
  for (const payload of ['{', 'null', '[]', '{}', '{"strategy":"old"}']) {
    assert.equal(cExitPresentation('TREND_EXIT_TRIGGERED', payload), null);
  }
  assert.equal(cExitPresentation('HARD_STOP_TRIGGERED', '{"strategy":"C"}'), null);
});
