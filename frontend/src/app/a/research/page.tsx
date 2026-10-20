'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bot, FlaskConical, Network, Sparkles, Target } from 'lucide-react';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import {
  CockpitPanel,
  CompactTable,
  Row,
  StageBadge,
  StatCard,
  WorkbenchPage,
  int,
  num,
  rows,
  signalNames,
  text,
} from '@/components/workspace/CockpitPrimitives';

export default function ResearchPage() {
  const { globalDate, setGlobalDate } = useGlobalStore();
  const [selectedTheme, setSelectedTheme] = useState('');

  const datesQuery = useQuery({ queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160) });
  const dates = rows<string>(datesQuery.data?.dates);
  const selectedDate = dates.includes(globalDate) ? globalDate : datesQuery.data?.latest || '';

  const researchQuery = useQuery({
    queryKey: ['researchSnapshot', selectedDate],
    queryFn: () => quantApi.getResearchSnapshot(selectedDate, 180),
    enabled: Boolean(selectedDate),
  });
  const lifecycleQuery = useQuery({
    queryKey: ['researchLifecycle', selectedDate],
    queryFn: () => quantApi.getLifecycle({ tradeDate: selectedDate, state: undefined, limit: 900 }),
    enabled: Boolean(selectedDate),
  });

  const researchRows = rows<Row>(researchQuery.data?.rows);
  const themes: Row[] = researchRows;
  const currentName = selectedTheme || text(themes[0]?.theme_name || themes[0]?.sector_name, '');
  const current = themes.find((row) => text(row.theme_name || row.sector_name) === currentName) || themes[0] || {};
  const lifecycle = rows<Row>(lifecycleQuery.data?.rows);
  const relatedStocks = lifecycle.filter((row) => text(row.sector_name) === text(current.sector_name || current.theme_name));

  const refresh = () => {
    datesQuery.refetch();
    researchQuery.refetch();
    lifecycleQuery.refetch();
  };

  return (
    <WorkbenchPage
      index="04"
      title="Research  产业研究池"
      subtitle="主题池、产业链传导、核心标的和 AI 研究结论。"
      date={selectedDate}
      dates={dates}
      onDateChange={setGlobalDate}
      onRefresh={refresh}
    >
      <div className="grid gap-3 2xl:grid-cols-[300px_1fr]">
        <CockpitPanel title="研究主题" icon={FlaskConical}>
          <div className="space-y-2">
            {themes.length === 0 ? (
              <div className="rounded-lg border border-border-primary bg-bg-primary/70 p-3 text-xs text-text-secondary">
                研究池未生成，research_stock_daily_snapshot 当前无记录。
              </div>
            ) : themes.slice(0, 8).map((row, index) => {
              const name = text(row.theme_name || row.sector_name);
              const active = name === currentName;
              return (
                <button
                  key={`${name}-${index}`}
                  type="button"
                  onClick={() => setSelectedTheme(name)}
                  className={active ? 'w-full rounded-lg border border-brand-blue/60 bg-brand-blue/15 p-3 text-left' : 'w-full rounded-lg border border-border-primary bg-bg-primary/70 p-3 text-left hover:border-brand-blue/40'}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[12px] font-black text-white">{name}</span>
                    <StageBadge value={row.theme_state || row.industry_state} />
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-text-secondary">
                    <span>热度 {num(row.heat_score ?? row.industry_strength_score, 0)}</span>
                    <span>信号 {int(row.signal_count ?? row.rps90_stock_count)}</span>
                    <span>{text(row.latest_date, selectedDate)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </CockpitPanel>

        <div className="grid gap-3">
          <CockpitPanel title="主题详情" icon={Sparkles}>
            <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-2xl font-black text-white">{currentName || '主题待选'}</h2>
                  <StageBadge value={current.theme_state || current.industry_state} />
                </div>
                <div className="mt-2 text-[11px] text-text-secondary">
                  更新 {text(current.latest_date, selectedDate)} · 热度 {num(current.heat_score ?? current.industry_strength_score, 0)} · 信号 {int(current.signal_count ?? current.rps90_stock_count)}
                </div>
                <div className="mt-4 grid gap-2 sm:grid-cols-4">
                  <StatCard label="主题热度" value={num(current.heat_score ?? current.industry_strength_score, 0)} tone="good" />
                  <StatCard label="相关标的" value={relatedStocks.length || int(current.signal_count)} sub="生命周期覆盖" tone="blue" />
                  <StatCard label="20日收益" value={current.return_20d === undefined ? '-' : num(current.return_20d, 2)} />
                  <StatCard label="研究评分" value={num(current.research_score ?? current.heat_score, 0)} tone="warn" />
                </div>
              </div>
              <div className="flex h-[150px] items-center justify-center rounded-lg border border-border-secondary bg-bg-primary/60 p-3 text-center text-xs text-text-secondary">
                暂无数据库研究趋势序列
              </div>
            </div>
          </CockpitPanel>

          <CockpitPanel title="产业链路径" icon={Network}>
            <div className="rounded-lg border border-border-primary bg-bg-primary/70 p-4 text-xs text-text-secondary">
              暂无数据库产业链路径；research_theme_nodes 当前无可展示节点。
            </div>
          </CockpitPanel>

          <div className="grid gap-3 2xl:grid-cols-[1fr_0.8fr]">
            <CockpitPanel title="核心标的映射" icon={Target}>
              <CompactTable
                rows={relatedStocks}
                limit={9}
                minWidth={760}
                rowKey={(row, index) => `${text(row.symbol)}-${index}`}
                columns={[
                  { key: 'symbol', label: '代码' },
                  { key: 'name', label: '名称' },
                  { key: 'sector_name', label: '行业' },
                  { key: 'lifecycle_state', label: '阶段', render: (row) => <StageBadge value={row.lifecycle_state} /> },
                  { key: 'signal_type', label: '买点', render: (row) => signalNames[text(row.signal_type, '')] || text(row.signal_type) },
                  { key: 'rps_20', label: 'RPS20', align: 'right', render: (row) => num(row.rps_20, 1) },
                  { key: 'structure_score', label: '评分', align: 'right', render: (row) => num(row.structure_score, 0) },
                ]}
              />
            </CockpitPanel>

            <CockpitPanel title="AI 总结" icon={Bot}>
              <div className="rounded-lg border border-border-secondary bg-bg-primary/70 p-3 text-[11px] text-text-secondary">
                暂无数据库 AI 研究结论。
              </div>
            </CockpitPanel>
          </div>
        </div>
      </div>
    </WorkbenchPage>
  );
}
