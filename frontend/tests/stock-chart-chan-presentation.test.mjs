import assert from 'node:assert/strict';
import test from 'node:test';
import { chanPointLabel, chanPointExplanation } from '../src/components/stock/stockChartChanPresentation.ts';
test('回绘模式明确买卖文字不是确认信号', () => {
  const point={index:1,price:10,label:'[类 2 买]',kind:'buy'};
  assert.equal(chanPointLabel(point,'repaint'),'回绘·类2买');
  assert.equal(chanPointLabel(point,'original'),'[类 2 买]');
  assert.equal(point.label,'[类 2 买]');
});
test('高低极点不再冒充买卖信号', () => {
  assert.equal(chanPointLabel({kind:'pivot',label:'买',direction:'low'},'repaint'),'低点');
  assert.equal(chanPointLabel({kind:'pivot',label:'卖',direction:'high'},'repaint'),'高点');
});
test('解释明确图形日、截止日和窗口起点', () => {
  const result=chanPointExplanation('2026-09-29','2026-09-30','2025-09-04');
  for(const text of ['2026-09-29','2026-09-30','2025-09-04','撤销','尚不能'])assert(result.includes(text));
});
