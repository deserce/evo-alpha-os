'use client';

import { TopbarTradeDate } from '@/components/layout/TopbarTradeDate';
import { Suspense, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, FlaskConical } from 'lucide-react';
import { researchApi, researchKeys, type ResearchFilters } from '@/lib/researchApi';
import { formatResearchTime, researchPageNumber, researchSyncMessage } from '@/lib/researchUtils';
import { quantApi } from '@/lib/api';
import { useGlobalStore } from '@/lib/store';
import { ResearchReportList } from '@/components/research/ResearchReportList';
import { CockpitPanel, CompactTable, type Row, rows } from '@/components/workspace/CockpitPrimitives';

import { AppShell } from '@/components/workspace/DashboardPrimitives';

const fieldClass = 'w-full rounded-lg border border-border-primary bg-bg-primary px-2 py-1.5 text-xs text-text-primary';
const buttonClass = 'rounded-lg border border-border-primary px-3 py-1.5 text-xs text-text-primary hover:border-brand-blue/60 disabled:opacity-40';

function ResearchPool() {
  const { globalDate, setGlobalDate } = useGlobalStore();
  const dates = useQuery({ queryKey: ['quantDates'], queryFn: () => quantApi.getDates(160), staleTime: 300000 });
  const dateList = rows<string>(dates.data?.dates);
  const selectedDate = globalDate || dates.data?.latest || '';
  const snapshot = useQuery({
    queryKey: ['researchSnapshot', selectedDate],
    queryFn: () => quantApi.getResearchSnapshot(selectedDate, 180), enabled: Boolean(selectedDate), staleTime: 300000,
  });
  return <CockpitPanel title="产业研究池 · 标的快照" icon={FlaskConical}>
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-text-secondary">按原始主题与标的关系展示已有研究快照。研报库与研究池分别维护。</p>
      <TopbarTradeDate value={selectedDate} dates={dateList} onChange={setGlobalDate} />
    </div>
    {snapshot.isLoading || dates.isLoading ? <p role="status" className="p-4 text-text-secondary">正在读取研究池…</p>
      : snapshot.isError || dates.isError ? <p role="alert" className="p-4 text-danger">研究池读取失败。<button className="ml-2 underline" onClick={() => { void dates.refetch(); void snapshot.refetch(); }}>重试</button></p>
      : !rows<Row>(snapshot.data?.rows).length ? <p className="p-4 text-sm text-text-secondary">所选日期暂无研究池快照。可在研报库检索资料。</p>
      : <CompactTable rows={rows<Row>(snapshot.data?.rows)} limit={180} rowKey={(row, index) => `${row.theme_name}-${row.symbol}-${index}`} columns={[
        { key: 'theme_name', label: '主题' }, { key: 'node_name', label: '产业链节点' },
        { key: 'symbol', label: '代码' }, { key: 'stock_name', label: '股票' },
        { key: 'research_status', label: '研究状态' }, { key: 'conviction_level', label: '确信度' },
      ]} />}
  </CockpitPanel>;
}

function ReportLibrary() {
  const search = useSearchParams();
  const router = useRouter();
  const filters: ResearchFilters = {
    q: search.get('q') || '', symbol: search.get('symbol') || '',
    report_type: ['company', 'industry', 'unknown'].includes(search.get('report_type') || '') ? search.get('report_type') as ResearchFilters['report_type'] : '',
    industry: search.get('industry') || '', org: search.get('org') || '',
    date_from: search.get('date_from') || '', date_to: search.get('date_to') || '',
    page: researchPageNumber(search.get('page')), page_size: 30,
  };
  const [validation, setValidation] = useState('');
  const query = useQuery({ queryKey: researchKeys.reports(filters), queryFn: ({ signal }) => researchApi.reports(filters, signal), staleTime: 30000, refetchOnMount: 'always', refetchOnWindowFocus: true, retry: 1 });
  const facets = useQuery({ queryKey: researchKeys.facets, queryFn: ({ signal }) => researchApi.facets(signal), staleTime: 30000, refetchOnMount: 'always', refetchOnWindowFocus: true, retry: 1 });
  const navigate = (params: URLSearchParams) => router.replace(`/a/research?${params}`, { scroll: false });
  const changePage = (page: number) => { const params = new URLSearchParams(search); params.set('page', String(page)); navigate(params); };
  return <CockpitPanel title="研报库" icon={BookOpen} className="research-library">
    <form key={`form:${search.toString()}`} onSubmit={(event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const params = new URLSearchParams();
      for (const [key, value] of data) if (String(value).trim()) params.set(key, String(value).trim());
      if (params.get('symbol') && !/^\d{6}$/.test(params.get('symbol')!)) { setValidation('股票代码须为 6 位数字。'); return; }
      if (params.get('date_from') && params.get('date_to') && params.get('date_from')! > params.get('date_to')!) { setValidation('开始日期不能晚于结束日期。'); return; }
      setValidation(''); navigate(params);
    }} className="mb-2 space-y-2">
      <div className="grid gap-x-3 gap-y-1.5 md:grid-cols-3 xl:grid-cols-4">
        <label className="text-xs text-text-secondary">标题 / 公司<input name="q" defaultValue={filters.q} maxLength={120} placeholder="搜索研报" className={fieldClass + ' mt-1'} /></label>
        <label className="text-xs text-text-secondary">股票代码<input name="symbol" defaultValue={filters.symbol} maxLength={6} inputMode="numeric" placeholder="如 600519" className={fieldClass + ' mt-1'} /></label>
        <label className="text-xs text-text-secondary">研报类型<select name="report_type" defaultValue={filters.report_type} className={fieldClass + ' mt-1'}><option value="">全部类型</option><option value="company">公司研报</option><option value="industry">行业研报</option><option value="unknown">待分类</option></select></label>
        <label className="text-xs text-text-secondary">机构<input name="org" maxLength={120} defaultValue={filters.org} list="research-orgs" placeholder="全部机构" className={fieldClass + ' mt-1'} /><datalist id="research-orgs">{facets.data?.organizations.map((name) => <option key={name} value={name} />)}</datalist></label>
        <label className="text-xs text-text-secondary">行业<input name="industry" maxLength={120} defaultValue={filters.industry} list="research-industries" placeholder="全部行业" className={fieldClass + ' mt-1'} /><datalist id="research-industries">{facets.data?.industries.map((name) => <option key={name} value={name} />)}</datalist></label>
        <label className="text-xs text-text-secondary">发布日期起<input name="date_from" type="date" defaultValue={filters.date_from} className={fieldClass + ' mt-1'} /></label>
        <label className="text-xs text-text-secondary">发布日期止<input name="date_to" type="date" defaultValue={filters.date_to} className={fieldClass + ' mt-1'} /></label>
        <div className="flex items-end gap-2"><button className={buttonClass + ' bg-brand-blue/15'} type="submit">查询</button><button className={buttonClass} type="button" onClick={() => { setValidation(''); router.replace('/a/research', { scroll: false }); }}>清空</button></div>
      </div>
      {validation && <p role="alert" className="text-sm text-danger">{validation}</p>}
      {facets.isError && <p className="text-xs text-text-secondary">筛选建议暂不可用，仍可手动输入机构或行业。</p>}
    </form>
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-y border-border-primary py-1 text-xs text-text-secondary">
      <span>{query.data ? `共 ${query.data.total.toLocaleString()} 篇 · 按发布日期排序` : '研报索引'}</span>
    </div>
    <div className="min-h-0 flex-1 overflow-auto overscroll-contain">
      <details className="border-b border-border-primary px-2 py-1.5 text-xs text-text-secondary">
        <summary className="cursor-pointer">更新状态 · 覆盖截至 {facets.data?.sync?.coverage_through || '未确认'}
          {facets.isError || ['failed', 'partial'].includes(facets.data?.sync?.status || '') ? ' · 需检查' : ''}
        </summary>
        <p className="mt-2">更新计划：{facets.data?.sync?.schedule || '状态暂不可用'}</p>
      <p className="mt-2">最近成功：{facets.data?.sync?.last_success_at ? `${formatResearchTime(facets.data.sync.last_success_at)}（北京时间）` : facets.data?.sync ? '暂无成功记录' : '未确认'} · 覆盖截至：{facets.data?.sync?.coverage_through || '未确认'}</p>
      <p role={facets.isError || ['failed', 'partial'].includes(facets.data?.sync?.status || '') ? 'alert' : 'status'} className={facets.isError || ['failed', 'partial'].includes(facets.data?.sync?.status || '') ? 'mt-2 text-warning' : 'mt-2'}>{facets.isError ? '更新状态读取失败，已显示的时间可能来自缓存。' : researchSyncMessage(facets.data?.sync)}</p>
      {facets.data?.sync?.last_attempt_at && <p className="mt-2">最近尝试：{formatResearchTime(facets.data.sync.last_attempt_at)}（北京时间）</p>}
      </details>
      <ResearchReportList key={`reports:${search.toString()}`} returnTo={`/a/research${search.toString() ? `?${search}` : ''}`} asOfDate={filters.date_to} notImported={query.data?.status === 'not_imported'} items={query.data?.items || []} loading={query.isLoading} error={query.isError} onRetry={() => void query.refetch()} />
    </div>
    <div className="mt-2 flex shrink-0 items-center justify-between gap-3 border-t border-border-primary pt-2">
      <span className="text-xs text-text-secondary">第 {filters.page} 页{query.data ? ` / ${Math.max(1, Math.ceil(query.data.total / 30))} 页` : ''} · 每页 30 篇</span>
      <div className="flex gap-2"><button className={buttonClass} disabled={filters.page === 1 || query.isFetching} onClick={() => changePage(filters.page! - 1)}>上一页</button><button className={buttonClass} disabled={!query.data || filters.page! * 30 >= query.data.total || query.isFetching} onClick={() => changePage(filters.page! + 1)}>下一页</button></div>
    </div>
  </CockpitPanel>;
}

export default function ResearchPage() {
  const [tab, setTab] = useState<'reports' | 'pool'>('reports');
  return (
    <AppShell>
      <section className="flex flex-col lg:h-[calc(100dvh-var(--evo-nav-height,64px))]">
        <header className="evo-page-header mt-2.5 flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="evo-page-title"><span className="evo-page-index">05</span> 研究中心</h1>
          <div role="group" aria-label="研究中心视图" className="stock-sidebar-tabs inline-flex items-center gap-1">
            {([{ id: 'reports', label: '研报库' }, { id: 'pool', label: '产业研究池' }] as const).map((view) => (
              <button
                key={view.id}
                type="button"
                aria-pressed={view.id === tab}
                onClick={() => setTab(view.id)}
                className={`px-3 ${view.id === tab
                  ? 'text-brand-blue-light'
                  : 'text-text-secondary hover:bg-bg-secondary hover:text-text-primary'}`}
              >
                {view.label}
              </button>
            ))}
          </div>
        </header>
        <div className="evo-command-space min-h-0 flex-1">
          {tab === 'reports' ? <Suspense fallback={<p role="status">正在读取研报…</p>}><ReportLibrary /></Suspense> : <ResearchPool />}
        </div>
      </section>
    </AppShell>
  );
}
