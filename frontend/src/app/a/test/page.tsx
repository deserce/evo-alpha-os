'use client';

import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';

export default function TestPage() {
  const { globalDate } = useGlobalStore();

  const { data: overview, isLoading } = useQuery({
    queryKey: ['test', globalDate],
    queryFn: () => marketApi.getStats(globalDate),
  });

  return (
    <div className="p-8 bg-bg-primary min-h-screen">
      <h1 className="text-2xl font-bold text-text-primary mb-4">日期响应测试</h1>

      <div className="mb-4 p-4 bg-bg-secondary rounded-lg">
        <p className="text-text-secondary mb-2">当前选择的日期: <span className="text-brand-blue font-mono">{globalDate}</span></p>
        <p className="text-text-secondary mb-2">后端返回的日期: <span className="text-brand-blue font-mono">{overview?.trade_date || '加载中...'}</span></p>
        <p className="text-text-secondary">涨家数: <span className="text-up font-mono">{overview?.up_count ?? '-'}</span></p>
        <p className="text-text-secondary">跌家数: <span className="text-success font-mono">{overview?.down_count ?? '-'}</span></p>
      </div>

      {isLoading && <p className="text-text-secondary">加载中...</p>}
    </div>
  );
}
