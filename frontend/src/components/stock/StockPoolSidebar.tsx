'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { Folder, Star } from 'lucide-react';
import { stockApi } from '@/lib/api';
import { Row, int, num, pct, rows, text, toNumber } from '@/components/workspace/CockpitPrimitives';

type StockPool = {
  pool_id: string;
  name: string;
  is_default?: number | boolean | null;
  stock_count?: number | null;
};

export default function StockPoolSidebar() {
  const params = useParams();
  const currentSymbol = String(params.symbol || '');

  const poolsQuery = useQuery({ queryKey: ['stockPoolsSidebar'], queryFn: () => stockApi.getPools() });
  const pools = rows<StockPool>(poolsQuery.data?.pools);
  const selectedPoolId = text(pools[0]?.pool_id, 'watchlist');
  const stocksQuery = useQuery({
    queryKey: ['stockPoolSidebarStocks', selectedPoolId],
    queryFn: () => stockApi.getPoolStocks(selectedPoolId),
    enabled: Boolean(selectedPoolId),
  });
  const stocks = rows<Row>(stocksQuery.data?.stocks);

  return (
    <aside className="flex h-full flex-col border-r border-border-primary bg-bg-secondary">
      <div className="border-b border-border-primary p-3">
        <div className="flex items-center gap-2 text-[12px] font-black text-white">
          <Star size={14} className="text-brand-blue-light" />
          自选股与分类池
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {pools.map((pool) => (
            <span
              key={pool.pool_id}
              className="inline-flex items-center gap-1 rounded border border-border-primary bg-bg-primary px-2 py-1 text-[10px] text-text-secondary"
            >
              <Folder size={11} />
              {text(pool.name)}
              <span className="font-mono text-text-tertiary">{int(pool.stock_count)}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {stocks.length === 0 ? (
          <div className="p-4 text-center text-[11px] leading-5 text-text-tertiary">
            暂无数据库自选股
          </div>
        ) : stocks.map((stock) => {
          const symbol = text(stock.symbol, '');
          const active = currentSymbol === symbol;
          return (
            <Link
              key={symbol}
              href={`/a/stock/${symbol}`}
              className={clsx(
                'block border-b border-border-secondary px-3 py-2 transition-colors',
                active ? 'bg-brand-blue/15' : 'hover:bg-brand-blue/10',
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[12px] font-black text-white">{text(stock.name, symbol)}</span>
                <span className={toNumber(stock.pct_chg) >= 0 ? 'font-mono text-[11px] text-danger' : 'font-mono text-[11px] text-success'}>
                  {pct(stock.pct_chg)}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-text-tertiary">
                <span className="font-mono">{symbol}</span>
                <span>收 {num(stock.close, 2)} · R20 {num(stock.rps_20, 0)}</span>
              </div>
            </Link>
          );
        })}
      </div>
    </aside>
  );
}
