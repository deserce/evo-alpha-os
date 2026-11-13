'use client';

import { TopbarTradeDate } from '@/components/layout/TopbarTradeDate';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { StockDetailPanel, PoolSidebar, WatchlistSidebarToggle, watchlistSidebarClass, watchlistContentClass, type PoolMember, type SortKey } from '@/components/stock/StockWorkspace';
import DailyHintsPanel, { type DailyHintsContext } from '@/components/stock/DailyHintsPanel';
import type { DailyHintSignal } from '@/lib/dailyHints';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import { useState, type ReactNode } from 'react';

async function request(path: string, method = 'GET') {
  const response = await fetch('/api/v1/' + path, { method, cache: 'no-store' });
  if (!response.ok) {
    const errors: Record<number, string> = {
      401: '请先登录。',
      403: '当前账户不可用。',
      422: '请输入六位股票代码。',
      503: '账户服务尚未配置。',
    };
    throw new Error(errors[response.status] ?? '请求失败，请稍后重试。');
  }
  return response.json();
}

export default function MyList({ listSwitcher }: { listSwitcher: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [editing, setEditing] = useState('');
  const [editingName, setEditingName] = useState('');
  const [keyword, setKeyword] = useState('');
  const [groupId, setGroupId] = useState(0);
  const [name, setName] = useState('');
  const [hintContext, setHintContext] = useState<DailyHintsContext>();
  const [selected, setSelected] = useState('');
  const globalDate = useGlobalStore((state) => state.globalDate);
  const setGlobalDate = useGlobalStore((state) => state.setGlobalDate);
  const dates = useQuery({queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160)});
  const date = globalDate || dates.data?.latest || '';
  const [sort, setSort] = useState<{key: SortKey; direction: 'asc' | 'desc'}>({key: 'symbol', direction: 'asc'});
  const client = useQueryClient();
  const identity = useQuery({
    queryKey: ['navigation-identity'],
    queryFn: () => request('users/me') as Promise<{ id: number; role: string; group_id: number | null }>,
    retry: false,
  });
  const key = ['personal-stocks', identity.data?.id];
  const stocks = useQuery({
    queryKey: key,
    queryFn: () => request('personal-stocks/groups') as Promise<{id: number; name: string; symbols: string[]}[]>,
    enabled: Boolean(identity.data),
    gcTime: 0,
    retry: false,
  });
  const group = stocks.data?.find((item) => item.id === (groupId || identity.data?.group_id)) ?? stocks.data?.[0];
  const hints = useQuery({
    queryKey: ['personal-group-hints', identity.data?.id, group?.id, date, group?.symbols],
    queryFn: () => request(`personal-stocks/groups/${group?.id}/hints?trade_date=${encodeURIComponent(date)}`) as Promise<{symbol: string; watch_state: string | null; reason?: string; current_price?: number; trigger_price?: number; invalidation_price?: number}[]>,
    enabled: Boolean(group && date), gcTime: 0,
  });
  const quotes = useQuery({
    queryKey: ['personal-group-quotes', identity.data?.id, group?.id, date, group?.symbols],
    queryFn: () => request(`personal-stocks/groups/${group?.id}/stocks?trade_date=${encodeURIComponent(date)}`) as Promise<PoolMember[]>,
    enabled: Boolean(group && date),
    gcTime: 0,
  });
  const edit = useMutation({
    mutationFn: async ({ action, value, id }: {action: string; value?: string; id?: number}) => {
      const response = await fetch('/api/v1/personal-stocks/groups', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action, value, group_id: id}),
      });
      if (!response.ok) throw new Error('无法保存个人分组，请检查名称或稍后重试。');
    },
    onSuccess: () => { setName(''); setEditing(''); return client.invalidateQueries({queryKey: key}); },
  });
  const update = useMutation({
    mutationFn: ({ code, method }: { code: string; method: 'PUT' | 'DELETE' }) =>
      fetch('/api/v1/personal-stocks/groups', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: method === 'PUT' ? 'add' : 'remove', value: code, group_id: group?.id}),
      }).then((response) => { if (!response.ok) throw new Error('无法更新个人分组。'); }),
    onSuccess: async (_, { method, code }) => {
      if (method === 'PUT') { setSelected(code); setKeyword(''); }
      await client.invalidateQueries({ queryKey: key });
    },
  });

  const results = useQuery({
    queryKey: ['my-watchlist-search', keyword],
    queryFn: async () => {
      const result = await request('stock/search?keyword=' + encodeURIComponent(keyword.trim()));
      return result.data.stocks as { symbol: string; name: string }[];
    },
    enabled: Boolean(keyword.trim() && identity.data),
  });
  const codes = stocks.error ? [] : group?.symbols ?? [];
  const active = codes.includes(selected) ? selected : codes[0];
  const watchSignals: DailyHintSignal[] = (hints.data ?? []).flatMap((row) => {
    const status = ({
      TRIGGERED: ['buy', '确认触发'],
      IN_ZONE: ['wait', '进入观察区'],
      INVALIDATED: ['risk', '已失效'],
      UNKNOWN: ['blocked', '数据不足'],
    } as const)[row.watch_state as 'TRIGGERED' | 'IN_ZONE' | 'INVALIDATED' | 'UNKNOWN'];
    if (!status) return [];
    return [{ symbol: row.symbol, name: quotes.data?.find((item) => item.symbol === row.symbol)?.name ?? row.symbol,
      source: '低吸', direction: status[0], label: status[1], reason: row.reason,
      currentPrice: row.current_price ?? undefined, triggerPrice: row.trigger_price ?? undefined,
      stopPrice: row.invalidation_price ?? undefined, confirmed: row.watch_state === 'TRIGGERED' }];
  });
  const pendingHints = hints.data?.filter((row) => row.watch_state === null).length ?? 0;
  const error = identity.error ?? stocks.error ?? update.error ?? results.error ?? edit.error ?? quotes.error;

  return (
    <section className="flex min-h-[calc(100dvh-var(--evo-nav-height,64px))] flex-col lg:h-[calc(100dvh-var(--evo-nav-height,64px))]">
      <header className="evo-page-header mx-3 mb-1.5 mt-2.5 flex shrink-0 flex-wrap items-center gap-2">
        {listSwitcher}
        <div className="ml-auto flex items-center gap-2">
          <TopbarTradeDate value={date} dates={(dates.data?.dates ?? []) as string[]} onChange={setGlobalDate} />
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {collapsed ? <div className="ml-3 my-2 shrink-0 rounded-lg border border-border-primary bg-bg-secondary/45 px-2 py-2">
          <WatchlistSidebarToggle collapsed onToggle={() => setCollapsed(false)} />
        </div> : <aside className={watchlistSidebarClass + " max-lg:mx-3 max-lg:w-auto"}>
          {error && <p role="alert" className="p-2 text-xs text-danger">{error.message}</p>}
          <PoolSidebar sidebarToggle={<WatchlistSidebarToggle collapsed={false} onToggle={() => setCollapsed(true)} />} canEdit={Boolean(identity.data)}
            pools={(stocks.data ?? []).map((item) => ({
              pool_id: String(item.id), name: item.name === 'Favorites' ? '收藏' : item.name, stock_count: item.symbols.length,
              is_default: item.name === 'Favorites',
            }))}
            selectedPoolId={group ? String(group.id) : ''} selectedSymbol={active ?? ''}
            members={quotes.data ?? codes.map((symbol) => ({symbol, name: symbol}))}
            keyword={keyword} searchRows={results.data ?? []}
            newPoolName={name} editingPoolId={editing} editingPoolName={editingName}
            isBusy={edit.isPending || update.isPending}
            sortKey={sort.key} sortDir={sort.direction}
            onKeywordChange={setKeyword} onNewPoolNameChange={(value) => setName(value.slice(0, 40))}
            onCreatePool={() => { if (name.trim() && !edit.isPending) edit.mutate({action: 'create', value: name}); }}
            onSelectPool={(id) => setGroupId(Number(id))}
            onStartEditPool={(pool) => { setEditing(pool.pool_id); setEditingName(pool.name); }}
            onEditingPoolNameChange={(value) => setEditingName(value.slice(0, 40))}
            onSavePool={() => { if (editingName.trim() && !edit.isPending) edit.mutate({
              action: 'rename', id: Number(editing), value: editingName,
            }); }}
            onCancelEditPool={() => setEditing('')}
            onDeletePool={(id) => {
              const target = stocks.data?.find((item) => String(item.id) === id);
              if (target && !edit.isPending && window.confirm('删除个人分组 ' + target.name + '？')) {
                edit.mutate({action: 'delete', id: target.id});
              }
            }}
            onAddStock={(code) => { if (group && !update.isPending && !codes.includes(code)) update.mutate({code, method: 'PUT'}); }}
            onSelectStock={setSelected}
            onRemoveStock={(code) => { if (!update.isPending) update.mutate({code, method: 'DELETE'}); }}
            onSort={(key) => setSort({key, direction: sort.key === key && sort.direction === 'asc' ? 'desc' : 'asc'})}
          />
        </aside>}
        <main className={watchlistContentClass}>
          <div className="min-h-[480px] flex-1 lg:min-h-0">
          {active ? <StockDetailPanel symbol={active} selectedDate={date || ''} showDetailLink onSelectSymbol={setSelected}
            unifiedHints={hintContext?.date === date && hintContext?.poolName === (group?.name === 'Favorites' ? '收藏' : group?.name) ? hintContext : undefined}
            dailyHintsPanel={<DailyHintsPanel personal date={date} poolId={`personal:${identity.data?.id}:${group?.id}`}
              poolName={group?.name === 'Favorites' ? '收藏' : group?.name ?? '个人分组'}
              members={codes.map((symbol) => ({ symbol }))} watchSignals={watchSignals}
              legacyActions={null} onSelectSymbol={setSelected} onContext={setHintContext}
              watchStatus={hints.isError ? '个人提示读取失败' : hints.isPending ? '读取中…' : pendingHints ? `${pendingHints} 只待计算` : ''} />} /> :
            <div className="grid h-full min-h-[280px] place-items-center text-sm text-text-tertiary">
              {stocks.isSuccess ? '搜索并添加股票后，即可在这里查看行情。' : '选择股票查看行情'}
            </div>}
          </div>
        </main>
      </div>
    </section>
  );
}
