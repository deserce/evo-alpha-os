'use client';

import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  HardDriveDownload,
  Play,
  RefreshCw,
  Zap,
} from 'lucide-react';
import { quantApi } from '@/lib/api';
import {
  AppShell,
  PageHeader,
  Badge,
  ToolbarButton,
  asList,
  int,
} from '@/components/workspace/DashboardPrimitives';

interface DataRow {
  [key: string]: unknown;
}

function formatSize(bytes: unknown) {
  const b = Number(bytes);
  if (!Number.isFinite(b)) return '-';
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(2)} GB`;
  if (b >= 1024 ** 2) return `${(b / 1024 ** 2).toFixed(0)} MB`;
  return `${(b / 1024).toFixed(0)} KB`;
}

const STATUS_LABELS: Record<string, string> = {
  ok: '正常',
  stale: '滞后',
  empty: '空表',
  missing: '缺失',
  duplicate: '重复',
};

const STATUS_COLORS: Record<string, string> = {
  ok: 'text-success',
  stale: 'text-warning',
  empty: 'text-danger',
  missing: 'text-danger',
  duplicate: 'text-danger',
};

const REPAIR_TASKS = [
  { id: 'rps_calculation', label: 'RPS 因子', desc: '计算相对强度' },
  { id: 'p0_calculation', label: 'P0 核心计算', desc: '强度/结构/生命周期' },
  { id: 'p1_calculation', label: 'P1 信号计算', desc: '买点/风控/研究' },
  { id: 'daily_workflow', label: '完整流水线', desc: '采集+计算+质量门' },
];

export default function DataPage() {
  const dataQuery = useQuery({
    queryKey: ['dataManagement'],
    queryFn: () => quantApi.getDataManagement(),
    refetchInterval: 15_000,
  });

  const [runningTask, setRunningTask] = useState<string | null>(null);
  const [taskResult, setTaskResult] = useState<{ task: string; status: string; log?: string } | null>(null);

  const payload = dataQuery.data ?? {};
  const database = payload.database ?? {};
  const tables = asList<DataRow>(payload.tables);
  const taskRuns = asList<DataRow>(payload.task_runs);
  const sourceCoverage = asList<DataRow>(payload.source_coverage);
  const statusCounts = (payload.status_counts ?? {}) as Record<string, number>;
  const sync = payload.sync ?? {};

  const okCount = statusCounts.ok ?? 0;
  const staleCount = statusCounts.stale ?? 0;
  const emptyCount = statusCounts.empty ?? 0;
  const abnormalCount = staleCount + emptyCount + (statusCounts.missing ?? 0) + (statusCounts.duplicate ?? 0);
  const totalCount = tables.length;
  const healthPct = totalCount > 0 ? Math.round((okCount / totalCount) * 100) : 0;
  const isHealthy = abnormalCount === 0;

  const latestRun = taskRuns[0];

  const repairMutation = useMutation({
    mutationFn: async (taskId: string) => {
      setRunningTask(taskId);
      const result = await quantApi.runDataTask({ task_id: taskId });
      return result as { run_id?: string; status?: string; log_path?: string };
    },
    onSuccess: (data, taskId) => {
      const task = REPAIR_TASKS.find((t) => t.id === taskId);
      setTaskResult({
        task: task?.label || taskId,
        status: '已提交，后台执行中',
        log: data?.log_path,
      });
      setTimeout(() => dataQuery.refetch(), 2000);
    },
    onError: (_error, taskId) => {
      const task = REPAIR_TASKS.find((t) => t.id === taskId);
      setTaskResult({
        task: task?.label || taskId,
        status: '执行失败',
      });
    },
    onSettled: () => setRunningTask(null),
  });

  const isLoading = dataQuery.isLoading && !dataQuery.data;
  const isRefreshing = dataQuery.isFetching || repairMutation.isPending;

  if (isLoading) {
    return (
      <main className="dashboard-page">
        <div className="dashboard-grid">
          <section className="dashboard-header dashboard-span-12">
            <div>
              <span className="dashboard-eyebrow">DATA</span>
              <h1>数据管理</h1>
            </div>
          </section>
          <section className="dashboard-panel dashboard-span-full">
            <div className="dashboard-empty">数据加载中…</div>
          </section>
        </div>
      </main>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="数据管理"
        eyebrow="DATA"
        icon={Database}
        actions={
          <ToolbarButton onClick={() => dataQuery.refetch()} active={isRefreshing}>
            <RefreshCw size={13} className={isRefreshing ? 'dashboard-spin' : ''} />
            刷新
          </ToolbarButton>
        }
      >
        最新数据 {String(database.latest_stock_date || '-')} · {formatSize(database.size_bytes)}
      </PageHeader>

      {/* 健康总览 — 一眼看清 */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 mb-4">
        <div className="cockpit-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-text-secondary">数据库健康度</span>
            {isHealthy ? (
              <CheckCircle2 size={16} className="text-success" />
            ) : (
              <AlertTriangle size={16} className="text-warning" />
            )}
          </div>
          <div className={`mt-2 text-3xl font-bold font-mono ${isHealthy ? 'text-success' : 'text-warning'}`}>
            {healthPct}%
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">
            {okCount}/{totalCount} 张表正常
          </div>
        </div>

        <div className="cockpit-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-text-secondary">异常表</span>
            <AlertTriangle size={16} className={abnormalCount > 0 ? 'text-warning' : 'text-success'} />
          </div>
          <div className={`mt-2 text-3xl font-bold font-mono ${abnormalCount > 0 ? 'text-warning' : 'text-success'}`}>
            {abnormalCount}
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">
            {staleCount > 0 ? `滞后 ${staleCount} 张` : ''}
            {staleCount > 0 && emptyCount > 0 ? ' · ' : ''}
            {emptyCount > 0 ? `空表 ${emptyCount} 张` : ''}
            {abnormalCount === 0 ? '一切正常' : ''}
          </div>
        </div>

        <div className="cockpit-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-text-secondary">最新数据日期</span>
            <Database size={16} className="text-brand-blue" />
          </div>
          <div className="mt-2 text-2xl font-bold font-mono text-text-primary">
            {String(database.latest_stock_date || '-')}
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">
            K 线数据 · {formatSize(database.size_bytes)}
          </div>
        </div>

        <div className="cockpit-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-text-secondary">最近执行</span>
            <Play size={16} className="text-text-tertiary" />
          </div>
          <div className="mt-2 text-base font-bold text-text-primary truncate">
            {String(latestRun?.task_title || latestRun?.task_id || '暂无')}
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">
            {latestRun ? `${String(latestRun.status || '-')} · ${String(latestRun.started_at || '-')}` : '无执行记录'}
          </div>
        </div>
      </div>

      {/* 一键修复 — 当有异常时高亮 */}
      {abnormalCount > 0 && (
        <div className="cockpit-card p-4 mb-4 border-warning/30">
          <div className="flex items-center gap-2 mb-3">
            <Zap size={16} className="text-warning" />
            <h2 className="text-sm font-bold text-text-primary">快速修复 · 滞后数据补算</h2>
            <span className="text-[11px] text-text-tertiary">
              K线已到 {String(database.latest_stock_date || '-')}，但 {staleCount} 张计算表滞后
            </span>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {REPAIR_TASKS.map((task) => (
              <button
                key={task.id}
                type="button"
                onClick={() => repairMutation.mutate(task.id)}
                disabled={!!runningTask}
                className="group rounded-lg border border-border-primary bg-bg-secondary/60 p-3 text-left transition-colors hover:border-brand-blue/50 hover:bg-brand-blue/10 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-bold text-text-primary">{task.label}</span>
                  {runningTask === task.id && <RefreshCw size={12} className="dashboard-spin text-brand-blue" />}
                </div>
                <div className="mt-1 text-[10px] text-text-tertiary">{task.desc}</div>
              </button>
            ))}
          </div>
          {taskResult && (
            <div className="mt-3 rounded-lg border border-border-primary bg-bg-secondary/40 p-3">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={14} className="text-success" />
                <span className="text-[12px] font-bold text-text-primary">{taskResult.task}</span>
                <span className="text-[11px] text-text-secondary">{taskResult.status}</span>
              </div>
              {taskResult.log && (
                <div className="mt-1 text-[10px] font-mono text-text-tertiary break-all">{taskResult.log}</div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 源覆盖矩阵 */}
      <div className="cockpit-card overflow-hidden mb-4">
        <div className="h-11 px-3 border-b border-border-primary/80 flex items-center justify-between gap-3 bg-bg-secondary/60">
          <div className="flex items-center gap-2">
            <Database size={14} className="text-brand-blue" />
            <h2 className="text-xs font-bold text-text-primary">源覆盖矩阵</h2>
            <span className="text-[10px] text-text-tertiary">缓存优先 · 缺数据不造数</span>
          </div>
        </div>
        <div className="grid gap-2 p-3 md:grid-cols-2 xl:grid-cols-3">
          {sourceCoverage.map((item) => {
            const status = String(item.status || 'unknown');
            const tablesInGroup = asList<DataRow>(item.tables);
            return (
              <div key={String(item.id || item.name)} className="rounded-lg border border-border-secondary/60 bg-bg-secondary/35 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[12px] font-bold text-text-primary">{String(item.name || '-')}</div>
                    <div className="mt-0.5 truncate text-[10px] text-text-tertiary">{String(item.source || '-')}</div>
                  </div>
                  <Badge status={status}>{STATUS_LABELS[status] || status}</Badge>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px]">
                  <div className="rounded border border-border-secondary/60 bg-bg-primary/35 px-2 py-1">
                    <div className="text-text-tertiary">最新日期</div>
                    <div className="font-mono text-text-secondary">{String(item.latest_date || '-')}</div>
                  </div>
                  <div className="rounded border border-border-secondary/60 bg-bg-primary/35 px-2 py-1">
                    <div className="text-text-tertiary">缓存行数</div>
                    <div className="font-mono text-text-secondary">{int(item.row_count)}</div>
                  </div>
                </div>
                <div className="mt-2 line-clamp-2 text-[10px] leading-4 text-text-secondary">{String(item.scope || '-')}</div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {tablesInGroup.slice(0, 5).map((table) => (
                    <span key={String(table.table_name)} className="rounded border border-border-secondary/50 bg-bg-primary/40 px-1.5 py-0.5 font-mono text-[9px] text-text-tertiary">
                      {String(table.table_name)}
                    </span>
                  ))}
                  {tablesInGroup.length > 5 && (
                    <span className="rounded border border-border-secondary/50 bg-bg-primary/40 px-1.5 py-0.5 text-[9px] text-text-tertiary">
                      +{tablesInGroup.length - 5}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
          {sourceCoverage.length === 0 && (
            <div className="col-span-full rounded-lg border border-border-secondary/60 bg-bg-secondary/40 p-4 text-center text-[11px] text-text-tertiary">
              数据库暂无源覆盖信息
            </div>
          )}
        </div>
      </div>

      {/* 表健康列表 */}
      <div className="cockpit-card overflow-hidden">
        <div className="h-11 px-3 border-b border-border-primary/80 flex items-center justify-between gap-3 bg-bg-secondary/60">
          <div className="flex items-center gap-2">
            <HardDriveDownload size={14} className="text-brand-blue" />
            <h2 className="text-xs font-bold text-text-primary">数据表状态</h2>
            <span className="text-[10px] text-text-tertiary">{totalCount} 张表</span>
          </div>
          <div className="flex items-center gap-3 text-[10px]">
            {Object.entries(statusCounts).map(([status, count]) => (
              <span key={status} className="flex items-center gap-1">
                <span className={`inline-block h-2 w-2 rounded-full ${
                  status === 'ok' ? 'bg-success' :
                  status === 'stale' ? 'bg-warning' :
                  'bg-danger'
                }`} />
                <span className="text-text-tertiary">{STATUS_LABELS[status] || status} {count}</span>
              </span>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto custom-scrollbar max-h-[520px] overflow-y-auto">
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 z-10 bg-[#0e1726]/95">
              <tr>
                <th className="px-3 py-2 text-[10px] font-bold text-text-secondary">状态</th>
                <th className="px-3 py-2 text-[10px] font-bold text-text-secondary">表名</th>
                <th className="px-3 py-2 text-[10px] font-bold text-text-secondary text-right">行数</th>
                <th className="px-3 py-2 text-[10px] font-bold text-text-secondary">最新日期</th>
                <th className="px-3 py-2 text-[10px] font-bold text-text-secondary text-right">滞后</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-secondary/60">
              {tables.map((row, index) => {
                const status = String(row.status || 'ok');
                const lagDays = Number(row.lag_days || 0);
                const isCritical = ['stock_daily_prices', 'quant_feature_rps', 'stock_lifecycle_state', 'timing_signals_daily', 'industry_strength_daily', 'market_strength_daily'].includes(String(row.table_name));
                return (
                  <tr key={`${row.table_name}-${index}`} className="hover:bg-bg-tertiary/40">
                    <td className="px-3 py-1.5">
                      <Badge status={status}>{STATUS_LABELS[status] || status}</Badge>
                    </td>
                    <td className="px-3 py-1.5 text-[11px] text-text-primary font-mono">
                      {String(row.table_name || '-')}
                      {isCritical && <span className="ml-1 text-[9px] text-brand-blue">核心</span>}
                    </td>
                    <td className="px-3 py-1.5 text-[11px] text-text-secondary text-right font-mono">
                      {int(row.row_count)}
                    </td>
                    <td className="px-3 py-1.5 text-[11px] text-text-secondary font-mono">
                      {String(row.max_date || '-')}
                    </td>
                    <td className={`px-3 py-1.5 text-[11px] text-right font-mono ${STATUS_COLORS[status] || 'text-text-tertiary'}`}>
                      {lagDays > 0 ? `${lagDays}天` : status === 'ok' ? '0' : '-'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 底部信息 */}
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div className="cockpit-card p-4">
          <h3 className="text-[12px] font-bold text-text-primary mb-3">最近任务记录</h3>
          <div className="space-y-2">
            {taskRuns.slice(0, 5).map((run, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg border border-border-secondary/60 bg-bg-secondary/40 px-3 py-2">
                <div className="flex items-center gap-2 min-w-0">
                  <Badge status={String(run.status)}>{String(run.status || '-')}</Badge>
                  <span className="text-[11px] text-text-primary truncate">{String(run.task_title || run.task_id || '-')}</span>
                </div>
                <span className="text-[10px] text-text-tertiary shrink-0">{String(run.started_at || '-')}</span>
              </div>
            ))}
            {taskRuns.length === 0 && <div className="text-[11px] text-text-tertiary">暂无执行记录</div>}
          </div>
        </div>

        <div className="cockpit-card p-4">
          <h3 className="text-[12px] font-bold text-text-primary mb-3">系统配置</h3>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg border border-border-secondary/60 bg-bg-secondary/40 p-2.5">
              <div className="text-[10px] text-text-tertiary">R2 对象存储</div>
              <div className={`text-[12px] font-bold ${sync.r2_configured ? 'text-success' : 'text-warning'}`}>
                {sync.r2_configured ? '已配置' : '未配置'}
              </div>
            </div>
            <div className="rounded-lg border border-border-secondary/60 bg-bg-secondary/40 p-2.5">
              <div className="text-[10px] text-text-tertiary">云端数据库</div>
              <div className={`text-[12px] font-bold ${sync.cloud_db_configured ? 'text-success' : 'text-text-tertiary'}`}>
                {sync.cloud_db_configured ? '已配置' : '未配置（预留）'}
              </div>
            </div>
            <div className="rounded-lg border border-border-secondary/60 bg-bg-secondary/40 p-2.5">
              <div className="text-[10px] text-text-tertiary">数据库路径</div>
              <div className="text-[11px] font-mono text-text-secondary truncate">local_quant.db</div>
            </div>
            <div className="rounded-lg border border-border-secondary/60 bg-bg-secondary/40 p-2.5">
              <div className="text-[10px] text-text-tertiary">K线强制同步</div>
              <div className={`text-[12px] font-bold ${sync.force_sync_kline ? 'text-warning' : 'text-text-secondary'}`}>
                {sync.force_sync_kline ? '开启' : '关闭'}
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
