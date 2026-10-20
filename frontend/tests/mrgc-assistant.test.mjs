import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveAssistantQuestion } from '../src/lib/mrgcAssistant.ts';

const stocks = [
  { symbol: '600001', name: '测试甲', sector_name: '机械' },
  { symbol: '600002', name: '测试乙', sector_name: '半导体' },
];
const resolve = (question, selected = '600001') => resolveAssistantQuestion(question, stocks, selected);

test('机会、卖点、风险、行业、日报意图对应确定性筛选', () => {
  for (const [question, intent, filter] of [
    ['今天有哪些机会', 'buy', 'buy'], ['卖点有哪些', 'exit', 'exit'],
    ['有哪些风险', 'risk', 'risk'], ['板块分布如何', 'sector', 'all'],
    ['总结今天日报', 'summary', 'all'], ['今天的买卖点', 'summary', 'all'],
  ]) {
    const result = resolve(question);
    assert.equal(result.intent, intent);
    assert.equal(result.filter, filter);
  }
});

test('当前股票和多股代码或名称对照，不改变输入事实', () => {
  assert.deepEqual(resolve('当前股票怎么样').symbols, ['600001']);
  const result = resolve('比较测试甲和600002的风险');
  assert.equal(result.intent, 'stocks');
  assert.equal(result.filter, 'risk');
  assert.deepEqual(result.symbols, ['600001', '600002']);
  assert.equal(stocks.length, 2);
  assert.equal(resolve('600001 600001').symbols.length, 1);
});

test('空、过长、格式错误和未知问题明确不支持', () => {
  assert.equal(resolve(' ').intent, 'unsupported');
  assert.match(resolve('字'.repeat(501)).title, /500/);
  assert.equal(resolveAssistantQuestion(null, stocks, '').intent, 'unsupported');
  assert.equal(resolveAssistantQuestion('今天机会', null, '').intent, 'unsupported');
  assert.equal(resolve('帮我订机票').intent, 'unsupported');
  assert.equal(resolve('测试甲写首诗').intent, 'unsupported');
  assert.equal(resolve('预测测试甲明天涨多少').intent, 'unsupported');
});

test('非成员和不存在的当前股票不伪造结果', () => {
  assert.equal(resolve('600099分析').intent, 'unsupported');
  assert.equal(resolve('当前股票怎么样', '600099').intent, 'unsupported');
  assert.deepEqual(resolve('16000012的日报').symbols, []);
});

test('成员与选择数量有界，最多20股票', () => {
  const many = Array.from({ length: 21 }, (_, i) => ({ symbol: String(600001 + i), name: `股票${i}` }));
  assert.equal(resolveAssistantQuestion(many.map(s => s.symbol).join(' '), many, '').intent, 'unsupported');
  assert.equal(resolveAssistantQuestion('今天机会', Array(301).fill(stocks[0]), '').intent, 'unsupported');
});


test('自然语言分组解析与意图方向并存',()=>{
 for(const [q,g,f] of [['公司机会','company','buy'],['自选风险','watchlist','risk'],
 ['MRGC卖点','mrgc','exit'],['交集买点','overlap','buy'],['公司与自选机会','overlap','buy'],
 ['看自选','watchlist','all'],['今天有哪些机会','all','buy']]){
  const result=resolve(q);assert.equal(result.group,g);assert.equal(result.filter,f);
  assert.notEqual(result.intent,'unsupported');
 }
});
test('指定股票与当前股票保留分组上下文',()=>{
 assert.equal(resolve('公司当前股票风险').group,'company');
 assert.equal(resolve('自选测试甲买点').group,'watchlist');
 assert.equal(resolve('自选预测明天涨多少').intent,'unsupported');
});
