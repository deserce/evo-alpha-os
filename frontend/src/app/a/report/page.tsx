'use client';

import { useQuery } from '@tanstack/react-query';
import { Bot, FileText, LineChart, PieChart, ShieldAlert, TrendingUp } from 'lucide-react';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import {
  CockpitPanel,
  CompactTable,
  ProgressLine,
  RingGauge,
  Row,
  StageBadge,
  StatCard,
  WorkbenchPage,
  clamp,
  int,
  num,
  pct,
  rows,
  text,
  toNumber,
} from '@/components/workspace/CockpitPrimitives';

function hasValue(value: unknown) {
  return value !== null && value !== undefined;
}

export default function ReviewPage() {
  const { globalDate, setGlobalDate } = useGlobalStore();
  const datesQuery = useQuery({ queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160) });
  const dates = rows<string>(datesQuery.data?.dates);
  const selectedDate = dates.includes(globalDate) ? globalDate : datesQuery.data?.latest || '';

  const reviewQuery = useQuery({
    queryKey: ['reviewSummary', selectedDate],
    queryFn: () => quantApi.getReviewSummary(selectedDate),
    enabled: Boolean(selectedDate),
  });
  const backtestQuery = useQuery({
    queryKey: ['reviewBacktest', selectedDate],
    queryFn: () => quantApi.getBacktestSummary({ endDate: selectedDate }),
    enabled: Boolean(selectedDate),
  });
  const briefQuery = useQuery({
    queryKey: ['reviewBrief', selectedDate],
    queryFn: () => quantApi.getAiBrief(selectedDate),
    enabled: Boolean(selectedDate),
  });

  const review = reviewQuery.data ?? {};
  const actions = rows<Row>(review.action_items);
  const risk = rows<Row>(review.risk_distribution);
  const backtest = rows<Row>(backtestQuery.data?.summary);
  const briefLines = rows<string>(briefQuery.data?.bullets);
  const sampleCount = backtest.reduce((sum, row) => sum + toNumber(row.count), 0);
  const riskTotal = risk.reduce((sum, row) => sum + toNumber(row.count), 0);
  const hasWinRate = hasValue(review.win_rate);
  const winRate = hasWinRate ? toNumber(review.win_rate) * 100 : null;

  const refresh = () => {
    datesQuery.refetch();
    reviewQuery.refetch();
    backtestQuery.refetch();
    briefQuery.refetch();
  };

  return (
    <WorkbenchPage
      index="08"
      title="Review  复盘进化"
      subtitle="把风险、买点表现、归因和 AI 复盘闭环到下一次执行。"
      date={selectedDate}
      dates={dates}
      onDateChange={setGlobalDate}
      onRefresh={refresh}
    >
      <div className="grid gap-3 sm:grid-cols-6">
        <StatCard label="交易次数" value={int(review.trade_count ?? sampleCount)} sub="本周期样本" />
        <StatCard label="高质量交易" value={int(review.quality_count ?? actions.length)} sub="符合系统" tone="blue" />
        <StatCard label="胜率" value={winRate === null ? '-' : `${winRate.toFixed(1)}%`} tone="good" />
        <StatCard label="盈亏比" value={hasValue(review.profit_factor) ? num(review.profit_factor) : '-'} tone="good" />
        <StatCard label="平均盈利" value={hasValue(review.avg_gain) ? pct(review.avg_gain) : '-'} tone="blue" />
        <StatCard label="平均亏损" value={hasValue(review.avg_loss) ? pct(review.avg_loss) : '-'} tone="bad" />
      </div>

      <div className="mt-3 grid gap-3 2xl:grid-cols-[0.95fr_1.05fr]">
        <CockpitPanel title="模式表现复盘" icon={LineChart}>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="flex h-[220px] items-center justify-center rounded-lg border border-border-secondary bg-bg-primary/60 p-3 text-xs text-text-secondary">
              暂无数据库模式表现序列
            </div>
            <div className="flex h-[220px] items-center justify-center rounded-lg border border-border-secondary bg-bg-primary/60 p-3 text-xs text-text-secondary">
              暂无数据库复盘趋势序列
            </div>
          </div>
        </CockpitPanel>

        <CockpitPanel title="收益曲线" icon={TrendingUp}>
          <div className="flex h-[220px] items-center justify-center rounded-lg border border-border-secondary bg-bg-primary/60 p-3 text-xs text-text-secondary">
            暂无数据库收益曲线
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2">
            <StatCard label="回测样本" value={int(sampleCount)} />
            <StatCard label="风险动作" value={int(actions.length)} tone={actions.length ? 'warn' : 'good'} />
            <StatCard label="风险分布" value={int(riskTotal)} />
          </div>
        </CockpitPanel>
      </div>

      <div className="mt-3 grid gap-3 2xl:grid-cols-[1fr_0.9fr]">
        <CockpitPanel title="买点表现" icon={FileText}>
          <CompactTable
            rows={backtest}
            limit={8}
            minWidth={760}
            rowKey={(row, index) => `${text(row.signal_type)}-${index}`}
            columns={[
              { key: 'signal_type', label: '信号' },
              { key: 'count', label: '样本', align: 'right', render: (row) => int(row.count) },
              { key: 'avg_return_5d', label: '5日均值', align: 'right', render: (row) => pct(row.avg_return_5d) },
              { key: 'win_rate_5d', label: '5日胜率', align: 'right', render: (row) => pct(row.win_rate_5d) },
              { key: 'avg_return_20d', label: '20日均值', align: 'right', render: (row) => pct(row.avg_return_20d) },
              { key: 'win_rate_20d', label: '20日胜率', align: 'right', render: (row) => pct(row.win_rate_20d) },
            ]}
          />
        </CockpitPanel>

        <CockpitPanel title="错误归因分析" icon={PieChart}>
          <div className="grid gap-3 md:grid-cols-[112px_1fr]">
            {winRate === null ? (
              <div className="flex h-[112px] items-center justify-center rounded-lg border border-border-secondary bg-bg-primary/60 text-center text-[11px] text-text-secondary">
                暂无胜率
              </div>
            ) : (
              <RingGauge value={clamp(winRate)} label="胜率" tone="good" />
            )}
            <div className="space-y-2">
              {risk.length ? risk.slice(0, 5).map((row, index) => (
                <ProgressLine
                  key={`${text(row.risk_status)}-${index}`}
                  label={text(row.risk_status)}
                  value={clamp(toNumber(row.count) / Math.max(1, riskTotal) * 100)}
                  tone={index < 2 ? 'warn' : 'blue'}
                  right={int(row.count)}
                />
              )) : <div className="rounded-lg border border-border-secondary bg-bg-primary/70 p-3 text-xs text-text-secondary">暂无数据库风险归因</div>}
            </div>
          </div>
        </CockpitPanel>
      </div>

      <div className="mt-3 grid gap-3 2xl:grid-cols-[0.9fr_1.1fr]">
        <CockpitPanel title="需要行动的计划" icon={ShieldAlert}>
          <CompactTable
            rows={actions}
            limit={8}
            minWidth={700}
            rowKey={(row, index) => `${text(row.symbol)}-${index}`}
            columns={[
              { key: 'symbol', label: '代码' },
              { key: 'name', label: '名称' },
              { key: 'sector_name', label: '行业' },
              { key: 'risk_status', label: '风险', render: (row) => <StageBadge value={row.risk_status} /> },
              { key: 'recommended_action', label: '动作' },
              { key: 'reduce_pct', label: '减仓', align: 'right', render: (row) => `${num(row.reduce_pct, 0)}%` },
              { key: 'r_multiple', label: 'R', align: 'right', render: (row) => num(row.r_multiple) },
            ]}
          />
        </CockpitPanel>

        <CockpitPanel title="AI 优化建议" icon={Bot}>
          <div className="space-y-2">
            {briefLines.length ? briefLines.slice(0, 5).map((line, index) => (
              <div key={`${line}-${index}`} className="rounded-lg border border-border-secondary bg-bg-primary/70 p-3 text-[11px] text-text-primary">
                <span className="mr-2 font-mono text-brand-blue-light">{index + 1}</span>
                {line}
              </div>
            )) : <div className="rounded-lg border border-border-secondary bg-bg-primary/70 p-3 text-xs text-text-secondary">暂无数据库 AI 优化建议</div>}
          </div>
        </CockpitPanel>
      </div>
    </WorkbenchPage>
  );
}
