import assert from 'node:assert/strict';
import test from 'node:test';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { api, marketApi, quantApi, stockApi } from '../src/lib/api.ts';

function captureRequests() {
  const previous = api.defaults.adapter;
  const calls = [];
  api.defaults.adapter = async (config) => {
    calls.push(config);
    return { data: { data: { ok: true } }, status: 200, statusText: 'OK',
      headers: {}, config };
  };
  return { calls, restore: () => { api.defaults.adapter = previous; } };
}

test('三个分钟接口传入明确周期、截止日和有界条数，超时20秒', async () => {
  const captured = captureRequests();
  try {
    await stockApi.getChartData('600519', '30min', '2026-09-30', 260);
    await quantApi.getSectorChart({ sectorName: '半导体', tradeDate: '2026-09-30',
      mode: 'vendor', period: '120min', limit: 260 });
    await marketApi.getMarketIndexKline('000001', 250, '2026-09-30', '30min');
    assert.equal(captured.calls.length, 3);
    assert.deepEqual(captured.calls.map((call) => call.params.period), ['30min', '120min', '30min']);
    assert.deepEqual(captured.calls.map((call) => call.timeout), [20000, 20000, 20000]);
    assert.equal(captured.calls[0].params.end_date, '2026-09-30');
    assert.equal(captured.calls[1].params.trade_date, '2026-09-30');
    assert.equal(captured.calls[2].params.date, '2026-09-30');
    assert.equal(captured.calls[0].params.limit, 260);
    assert.equal(captured.calls[1].params.mode, 'vendor');
    assert.equal(captured.calls[2].params.days, 250);
  } finally { captured.restore(); }
});

test('省略周期时仍默认日线，板块保留local，指数保留latest语义', async () => {
  const captured = captureRequests();
  try {
    await stockApi.getChartData('600519');
    await quantApi.getSectorChart({ sectorName: '半导体' });
    await marketApi.getMarketIndexKline('000001');
    assert.deepEqual(captured.calls.map((call) => call.params.period), ['daily', 'daily', 'daily']);
    assert.equal(captured.calls[1].params.mode, 'local');
    assert.equal(captured.calls[2].params.date, 'latest');
  } finally { captured.restore(); }
});

test('分钟接口失败原样拒绝，不在客户端偷偷切回日线或重复请求', async () => {
  const previous = api.defaults.adapter;
  let count = 0;
  api.defaults.adapter = async () => {
    count += 1;
    throw new Error('minute_source_unavailable_fixture');
  };
  try {
    await assert.rejects(marketApi.getMarketIndexKline('000001', 250, undefined, '120min'),
      /minute_source_unavailable_fixture/);
    assert.equal(count, 1);
  } finally { api.defaults.adapter = previous; }
});

test('HTTP 200源不可用虽仍在缓存新鲜期，手动refetch可以恢复且只新增一次请求', async () => {
  const previous = api.defaults.adapter;
  const client = new QueryClient();
  let count = 0;
  let unsubscribe;
  api.defaults.adapter = async (config) => {
    count += 1;
    return { data: { data: count === 1
      ? { status: 'unavailable', items: [], message: 'fixture source unavailable' }
      : { status: 'ok', items: [{ tradeDate: '2026-09-30 15:00', close: 10 }] } },
      status: 200, statusText: 'OK', headers: {}, config };
  };
  const options = { queryKey: ['minute-retry-fixture', '000001', '30min'],
    queryFn: () => marketApi.getMarketIndexKline('000001', 250, '2026-09-30', '30min'),
    staleTime: 120000, retry: false, refetchOnWindowFocus: false };
  try {
    assert.equal((await client.fetchQuery(options)).status, 'unavailable');
    const observer = new QueryObserver(client, options);
    unsubscribe = observer.subscribe(() => {});
    assert.equal(observer.getCurrentResult().isStale, false);
    assert.equal(count, 1);
    const request = observer.refetch();
    assert.equal(observer.getCurrentResult().isFetching, true);
    const result = await request;
    assert.equal(result.isFetching, false);
    assert.equal(result.data.status, 'ok');
    assert.equal(result.data.items[0].tradeDate, '2026-09-30 15:00');
    assert.equal(count, 2);
  } finally {
    unsubscribe?.();
    client.clear();
    api.defaults.adapter = previous;
  }
});
