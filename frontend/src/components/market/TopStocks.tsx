'use client';

import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import { Trophy } from 'lucide-react';

export default function TopStocks({ tradeDate }: { tradeDate?: string }) {
  const { data: limitData, isLoading } = useQuery({
    queryKey: ['topStocks', tradeDate],
    queryFn: () => marketApi.getLimitBoards(tradeDate),
    refetchInterval: 30000,
  });

  const stocks = limitData?.limit_up?.top_stocks || [];

  if (isLoading) {
    return (
      <div className="w-full bg-bg-secondary border border-border-primary rounded-lg p-6">
        <div className="flex items-center justify-center h-[200px] text-text-secondary text-sm animate-pulse font-mono">
          Loading Stocks Data...
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-bg-secondary border border-border-primary rounded-lg overflow-hidden">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-primary/50">
        <div className="flex items-center gap-2">
          <Trophy size={13} className="text-up" />
          <h3 className="text-text-primary text-xs font-bold">
            代表个股
          </h3>
        </div>
        <span className="text-xs text-text-secondary">
          涨停龙头
        </span>
      </div>

      {/* 股票列表 */}
      <div className="p-4 space-y-2">
        {stocks.length === 0 ? (
          <div className="rounded-lg border border-border-primary bg-bg-primary/60 p-4 text-center text-xs text-text-secondary">
            暂无数据库涨停代表个股
          </div>
        ) : stocks.map((stock: any, idx: number) => (
          <div
            key={idx}
            className="bg-bg-tertiary border border-border-primary/50 rounded-lg p-3 hover:border-up/30 hover:bg-up/5 transition-colors cursor-pointer"
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-bold text-text-primary">{stock.name}</span>
              <span className="text-xs text-brand-blue font-mono">{stock.code}</span>
            </div>
            <div className="text-xs text-text-secondary">{stock.reason}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
