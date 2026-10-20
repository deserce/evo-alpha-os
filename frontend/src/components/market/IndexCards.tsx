'use client';

import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import { TrendingUp, TrendingDown } from 'lucide-react';
import { clsx } from 'clsx';

// 三大指数代码
const MAJOR_INDICES = [
  { code: '000001', name: '上证指数', shortName: '上证' },
  { code: '399001', name: '深证成指', shortName: '深证' },
  { code: '399006', name: '创业板指', shortName: '创业板' },
];

export default function IndexCards() {
  // 获取三大指数最新数据
  const { data: indicesData, isLoading } = useQuery({
    queryKey: ['majorIndices'],
    queryFn: () => marketApi.getIndexLatest(),
    refetchInterval: 30000, // 30秒刷新
  });

  if (isLoading) {
    return (
      <div
      className="grid gap-3"
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
      }}
    >
        {MAJOR_INDICES.map((index, i) => (
          <div
            key={index.code}
            className="bg-bg-secondary border border-border-primary rounded-lg p-4 animate-pulse"
            style={{ animationDelay: `${i * 100}ms` }}
          >
            <div className="h-3 bg-bg-tertiary rounded w-20 mb-2"></div>
            <div className="h-7 bg-bg-tertiary rounded w-28 mb-2"></div>
            <div className="flex gap-2">
              <div className="h-5 bg-bg-tertiary rounded w-16"></div>
              <div className="h-5 bg-bg-tertiary rounded w-14"></div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  // 提取三大指数数据
  const getIndexData = (code: string) => {
    if (!indicesData?.indices) return null;
    const item = indicesData.indices.find((i: any) => i.index_code === code);
    if (!item) return null;

    // 映射字段名以匹配前端期望
    return {
      ...item,
      change_pct: item.pct_chg,  // 映射 pct_chg 到 change_pct
      change: item.change,
    };
  };

  return (
    <div
      className="grid gap-3"
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
      }}
    >
      {MAJOR_INDICES.map((index) => {
        const data = getIndexData(index.code);

        if (!data) {
          return (
            <div
              key={index.code}
              className="bg-bg-secondary border border-border-primary rounded-xl p-4 sm:p-5"
            >
              <div className="text-text-tertiary text-xs font-bold uppercase tracking-wider mb-2">
                {index.shortName}
              </div>
              <div className="text-text-disabled text-sm sm:text-lg font-mono">暂无数据</div>
            </div>
          );
        }

        const isUp = data.change_pct >= 0;
        const isFlat = data.change_pct === 0;

        return (
          <div
            key={index.code}
            className="bg-bg-secondary border border-border-primary rounded-lg p-4 hover:shadow-md transition-shadow duration-200 cursor-pointer"
          >
            {/* 指数名称 */}
            <div className="text-text-tertiary text-xs font-medium mb-2">
              {index.name}
            </div>

            {/* 指数点位 */}
            <div className={clsx(
              "text-2xl font-bold font-mono tracking-tight mb-2",
              isUp ? "text-up" : isFlat ? "text-text-secondary" : "text-success"
            )}>
              {data.close ? data.close.toFixed(2) : '--'}
            </div>

            {/* 涨跌幅信息 */}
            <div className="flex items-center gap-2">
              <div className={clsx(
                "text-base font-mono font-semibold",
                isUp ? "text-up" : isFlat ? "text-text-secondary" : "text-success"
              )}>
                {isUp ? '+' : ''}{data.change?.toFixed(2) || '--'}
              </div>
              <div className={clsx(
                "text-sm font-mono",
                isUp ? "text-up" : isFlat ? "text-text-secondary" : "text-success"
              )}>
                {isUp ? '+' : ''}{data.change_pct?.toFixed(2) || '--'}%
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
