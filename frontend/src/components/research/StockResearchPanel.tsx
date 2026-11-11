'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { quantApi } from '@/lib/api';
import { resolveResearchCutoff, type ResearchDateMode } from '@/lib/researchUtils';
import { researchApi, researchKeys, type ResearchFilters } from '@/lib/researchApi';
import { ResearchReportList } from './ResearchReportList';

interface StockResearchPanelProps { symbol: string; asOfDate?: string; forceAsOf?: boolean }

export function StockResearchPanel({ symbol, asOfDate, forceAsOf = false }: StockResearchPanelProps) {
  const context = `${symbol}-${asOfDate || 'latest'}-${forceAsOf}`;
  const [choice, setChoice] = useState<{ context: string; mode: ResearchDateMode } | null>(null);
  const mode = choice?.context === context ? choice.mode : 'auto';
  const setMode = (nextMode: ResearchDateMode) => setChoice({ context, mode: nextMode });
  const dates = useQuery<{ latest?: string }>({
    queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160), staleTime: 300000,
  });
  const cutoff = resolveResearchCutoff(asOfDate, dates.data?.latest, mode, forceAsOf);
  const awaitingDates = Boolean(asOfDate) && mode === 'auto' && !forceAsOf && dates.isLoading;
  const filters: ResearchFilters = {
    symbol, report_type: 'company', page: 1, page_size: 3,
    ...(cutoff ? { date_to: cutoff } : {}),
  };
  const query = useQuery({
    queryKey: researchKeys.reports(filters),
    placeholderData: undefined,
    queryFn: ({ signal }) => researchApi.reports(filters, signal),
    enabled: /^\d{6}$/.test(symbol) && !awaitingDates, staleTime: 30000, refetchOnMount: 'always', refetchOnWindowFocus: true, retry: 1,
  });
  const params = new URLSearchParams({ symbol, report_type: 'company' });
  if (filters.date_to) params.set('date_to', filters.date_to);
  return <section className="rounded-lg border border-border-primary bg-bg-primary/40 p-3">
    <header className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-text-primary">公司研报</h3>
      <Link href={`/a/research?${params}`} className="text-xs text-brand-blue-light">查看全部研报 →</Link>
    </header>
    <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="研报日期范围">
      <button type="button" aria-pressed={!cutoff} className={`rounded border px-2 py-1 text-xs text-brand-blue-light ${!cutoff ? 'border-brand-blue/60 bg-brand-blue/15' : 'border-border-primary'}`} onClick={() => setMode('latest')}>最新研报</button>
      <button type="button" aria-pressed={Boolean(cutoff)} disabled={!asOfDate} className={`rounded border px-2 py-1 text-xs text-brand-blue-light disabled:opacity-40 ${cutoff ? 'border-brand-blue/60 bg-brand-blue/15' : 'border-border-primary'}`} onClick={() => setMode('asof')}>截至行情日</button>
    </div>
    <p className="mt-2 text-xs text-text-secondary">{awaitingDates ? '正在确认行情日期…' : cutoff ? `仅展示 ${cutoff} 及以前发布的研报。` : '展示最新已入库研报，包括行情日之后及周末发布的研报。'}</p>
    {dates.isError && mode === 'auto' && <p className="mt-1 text-xs text-warning">最新行情日暂不可用，已按所选日期保守截止；可手动切换最新研报。</p>}
    <p className="mt-1 text-xs text-text-secondary">来源明确覆盖该股票{filters.date_to ? ` · 截至 ${filters.date_to}` : ''}</p>
    <p className="mt-1 text-xs text-text-secondary">已入库研报 · 重新打开或切回页面时刷新列表 · 更新计划与进度见研究中心</p>
    <ResearchReportList returnTo={`/a/research?${params}`} asOfDate={filters.date_to} notImported={query.data?.status === 'not_imported'} items={query.data?.items || []} loading={awaitingDates || query.isLoading} error={query.isError} onRetry={() => void query.refetch()} compact />
    {query.data?.items[0]?.industry_name?.trim() && <RelatedIndustryReports industry={query.data.items[0].industry_name.trim()} asOfDate={filters.date_to} />}
  </section>;
}


function RelatedIndustryReports({ industry, asOfDate }: { industry: string; asOfDate?: string }) {
  const context = `${industry}-${asOfDate || 'latest'}`;
  const [expandedContext, setExpandedContext] = useState<string | null>(null);
  const expanded = expandedContext === context;
  const filters: ResearchFilters = {
    report_type: 'industry', industry, page: 1, page_size: 3,
    ...(asOfDate ? { date_to: asOfDate } : {}),
  };
  const query = useQuery({
    queryKey: researchKeys.reports(filters),
    placeholderData: undefined,
    queryFn: ({ signal }) => researchApi.reports(filters, signal),
    enabled: expanded, staleTime: 30000, refetchOnMount: 'always', refetchOnWindowFocus: true, retry: 1,
  });
  const params = new URLSearchParams({ report_type: 'industry', industry });
  if (asOfDate) params.set('date_to', asOfDate);
  return <div className="mt-2 border-t border-border-primary pt-3">
    <button type="button" aria-expanded={expanded} className="text-xs text-brand-blue-light" onClick={() => setExpandedContext(expanded ? null : context)}>相关行业研报：{industry} {expanded ? '收起 ↑' : '展开 ↓'}</button>
    {expanded && <>
      <p className="mt-2 text-xs text-text-secondary">行业关联来自最近公司研报的行业分类，不代表研报直接覆盖该公司。</p>
      <ResearchReportList items={query.data?.items || []} notImported={query.data?.status === 'not_imported'} loading={query.isLoading} error={query.isError} onRetry={() => void query.refetch()} compact returnTo={`/a/research?${params}`} asOfDate={asOfDate} />
      <Link href={`/a/research?${params}`} className="text-xs text-brand-blue-light">查看全部行业研报 →</Link>
    </>}
  </div>;
}
