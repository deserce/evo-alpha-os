'use client';

import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import { TrendingUp, TrendingDown, BarChart3 } from 'lucide-react';
import { clsx } from 'clsx';

export default function LimitAnalysis({ tradeDate }: { tradeDate?: string }) {
  const { data: limitData, isLoading } = useQuery({
    queryKey: ['limitAnalysis', tradeDate],
    queryFn: () => marketApi.getLimitBoards(tradeDate),
    refetchInterval: 30000,
  });

  const analysisData = limitData || {
    limit_up: { total: 0, by_sector: [], top_stocks: [] },
    limit_down: { total: 0, by_sector: [], top_stocks: [] },
  };

  if (isLoading) {
    return (
      <div className="w-full bg-bg-secondary border border-border-primary rounded-2xl p-6">
        <div className="flex items-center justify-center h-[300px] text-text-secondary text-sm animate-pulse font-mono">
          Loading Analysis Data...
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-bg-secondary border border-border-primary rounded-lg overflow-hidden">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-primary/50">
        <div className="flex items-center gap-2">
          <BarChart3 size={13} className="text-text-secondary" />
          <h3 className="text-text-primary text-xs font-bold">
            涨跌停分析
          </h3>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <div className="flex items-center gap-1">
            <TrendingUp size={12} className="text-up" />
            <span className="text-text-secondary">涨停</span>
            <span className="text-up font-bold font-mono">{analysisData.limit_up.total}</span>
          </div>
          <div className="flex items-center gap-1">
            <TrendingDown size={12} className="text-down" />
            <span className="text-text-secondary">跌停</span>
            <span className="text-down font-bold font-mono">{analysisData.limit_down.total}</span>
          </div>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-primary/50 bg-bg-tertiary/30">
              <th className="px-4 py-2 text-left text-xs font-semibold text-text-tertiary">
                涨停板块
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-text-tertiary">
                数量
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-text-tertiary">
                占比
              </th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-text-tertiary w-1/4">
                分布
              </th>
            </tr>
          </thead>
          <tbody>
            {analysisData.limit_up.by_sector.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-xs text-text-secondary">
                  暂无数据库涨停板块分布
                </td>
              </tr>
            ) : analysisData.limit_up.by_sector.map((sector: any, idx: number) => (
              <tr key={idx} className="border-b border-border-primary/50 hover:bg-bg-tertiary/10 transition-colors">
                <td className="px-4 py-2 text-xs text-text-primary font-medium">
                  {sector.sector}
                </td>
                <td className="px-4 py-2 text-right">
                  <span className="text-sm font-bold text-up font-mono">{sector.count}</span>
                  <span className="text-xs text-text-secondary ml-1">家</span>
                </td>
                <td className="px-4 py-2 text-right text-xs text-text-secondary font-mono">
                  {sector.percentage.toFixed(1)}%
                </td>
                <td className="px-4 py-2">
                  <div className="w-full bg-bg-tertiary rounded-full h-1.5 overflow-hidden">
                    <div
                      className="h-full bg-up rounded-full transition-all duration-500"
                      style={{ width: `${sector.percentage}%` }}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* 跌停表格 */}
        <table className="w-full border-t border-border-primary/50">
          <thead>
            <tr className="border-b border-border-primary/50 bg-bg-tertiary/30">
              <th className="px-4 py-2 text-left text-xs font-semibold text-text-tertiary">
                跌停板块
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-text-tertiary">
                数量
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-text-tertiary">
                占比
              </th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-text-tertiary w-1/4">
                分布
              </th>
            </tr>
          </thead>
          <tbody>
            {analysisData.limit_down.by_sector.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-xs text-text-secondary">
                  暂无数据库跌停板块分布
                </td>
              </tr>
            ) : analysisData.limit_down.by_sector.map((sector: any, idx: number) => (
              <tr key={idx} className="border-b border-border-primary/50 hover:bg-bg-tertiary/10 transition-colors">
                <td className="px-4 py-2 text-xs text-text-primary font-medium">
                  {sector.sector}
                </td>
                <td className="px-4 py-2 text-right">
                  <span className="text-sm font-bold text-down font-mono">{sector.count}</span>
                  <span className="text-xs text-text-secondary ml-1">家</span>
                </td>
                <td className="px-4 py-2 text-right text-xs text-text-secondary font-mono">
                  {sector.percentage.toFixed(1)}%
                </td>
                <td className="px-4 py-2">
                  <div className="w-full bg-bg-tertiary rounded-full h-1.5 overflow-hidden">
                    <div
                      className="h-full bg-down rounded-full transition-all duration-500"
                      style={{ width: `${sector.percentage}%` }}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
