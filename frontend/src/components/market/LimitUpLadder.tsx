'use client';

import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import { Flame } from 'lucide-react';
import { clsx } from 'clsx';

// 连板层级定义
const LADDER_LEVELS = [10, 9, 8, 7, 6, 5, 4, 3, 2];

export default function LimitUpLadder({ tradeDate }: { tradeDate?: string }) {
  const { data: limitData, isLoading } = useQuery({
    queryKey: ['limitBoards', tradeDate],
    queryFn: () => marketApi.getLimitBoards(tradeDate),
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <div className="w-full bg-bg-secondary border border-border-primary rounded-lg p-6">
        <div className="flex items-center justify-center h-[200px] text-text-secondary text-sm animate-pulse font-mono">
          Loading Ladder Data...
        </div>
      </div>
    );
  }

  const ladderData: Record<string, number> = limitData?.ladder || {};

  // 计算总数和最大值（用于比例计算）
  const totalCount = Object.values(ladderData).reduce((sum: number, count: any) => sum + (count || 0), 0);
  const maxCount = Math.max(0, ...Object.values(ladderData).map((c: any) => c || 0));

  return (
    <div className="w-full bg-bg-secondary border border-border-primary rounded-lg overflow-hidden">
      {/* 连板天梯表格 */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-primary/50 bg-bg-tertiary/30">
              <th className="px-4 py-2 text-left text-xs font-semibold text-text-tertiary">
                连板层级
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-text-tertiary">
                数量
              </th>
              <th className="px-4 py-2 text-right text-xs font-semibold text-text-tertiary">
                占比
              </th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-text-tertiary w-1/3">
                热度
              </th>
            </tr>
          </thead>
          <tbody>
            {LADDER_LEVELS.map((level) => {
              const count = ladderData[String(level)] || 0;
              const percentage = maxCount > 0 ? (count / maxCount) * 100 : 0;
              const totalPercentage = totalCount > 0 ? (count / totalCount) * 100 : 0;
              const isHighLevel = level >= 5;
              const hasData = count > 0;

              return (
                <tr key={level} className="border-b border-border-primary/50 hover:bg-bg-tertiary/10 transition-colors">
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className={clsx(
                        "text-xs font-bold px-1.5 py-0.5 rounded",
                        isHighLevel ? "bg-up/10 text-up" : "bg-text-secondary/10 text-text-secondary"
                      )}>
                        {level}板
                      </span>
                      {isHighLevel && hasData && (
                        <Flame size={10} className="text-up animate-pulse" />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span className={clsx(
                      "text-sm font-bold font-mono",
                      hasData ? (isHighLevel ? "text-up" : "text-text-primary") : "text-text-disabled"
                    )}>
                      {count}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span className={clsx(
                      "text-xs font-mono",
                      hasData ? "text-text-secondary" : "text-text-disabled"
                    )}>
                      {hasData ? `${totalPercentage.toFixed(1)}%` : '--'}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    {hasData ? (
                      <div className="w-full bg-bg-tertiary rounded-full h-1.5 overflow-hidden">
                        <div
                          className={clsx(
                            "h-full rounded-full transition-all duration-500",
                            isHighLevel ? "bg-up" : "bg-text-secondary"
                          )}
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                    ) : (
                      <div className="w-full bg-bg-tertiary/30 rounded-full h-1.5" />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 底部统计 */}
      <div className="px-4 py-2 border-t border-border-primary/50 flex justify-between items-center bg-bg-tertiary/30">
        <span className="text-text-tertiary text-xs">总计</span>
        <span className="text-up text-sm font-bold font-mono">{totalCount}家</span>
      </div>
    </div>
  );
}
