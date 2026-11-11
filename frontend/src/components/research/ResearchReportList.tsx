'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { researchApi } from '@/lib/researchApi';
import { formatResearchTime, researchStockHref, safeResearchUrl } from '@/lib/researchUtils';
import type { ResearchReport } from '@/lib/researchApi';

export function ResearchReportList({ items, loading, error, onRetry, compact = false, returnTo, asOfDate, notImported }: {
  items: ResearchReport[];
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  compact?: boolean;
  returnTo?: string;
  asOfDate?: string;
  notImported?: boolean;
}) {
  const scope = `${returnTo || ''}|${asOfDate || ''}`;
  const [selection, setSelection] = useState<{ scope: string; id: string } | null>(null);
  const selectedId = selection?.scope === scope && items.some((item) => item.id === selection.id)
    ? selection.id : null;
  const setSelectedId = (id: string | null) => setSelection(id ? { scope, id } : null);
  const detail = useQuery({
    queryKey: ['researchReport', selectedId],
    queryFn: ({ signal }) => researchApi.report(selectedId!, signal),
    enabled: selectedId !== null && items.some((item) => item.id === selectedId), staleTime: 300000, retry: 1,
  });
  if (loading) return <ul className="divide-y divide-border-primary"><li role="status" className="p-4 text-sm text-text-secondary">正在读取研报…</li></ul>;
  if (error) return <ul className="divide-y divide-border-primary"><li role="alert" className="p-4 text-sm text-danger">研报读取失败，请稍后重试。
    {onRetry && <button type="button" onClick={onRetry} className="ml-3 underline">重试</button>}
  </li></ul>;
  if (notImported) return <ul className="divide-y divide-border-primary"><li role="status" className="p-4 text-sm text-text-secondary">研报库尚未导入，导入资料后即可查看公司与行业研报。</li></ul>;
  if (!items.length) return <ul className="divide-y divide-border-primary"><li className="p-4 text-sm text-text-secondary">当前条件下暂无研报。</li></ul>;
  return <ul className="divide-y divide-border-primary">
    {items.map((report) => {
      const pdf = safeResearchUrl(report.pdf_url);
      const original = safeResearchUrl(report.original_url);
      const primary = original || pdf;
      return <li key={report.id} className={compact ? 'py-3' : 'p-4 hover:bg-bg-primary/40'}>
        <div className={compact ? 'flex flex-col gap-2' : 'flex flex-wrap items-start justify-between gap-3'}>
          <div className={compact ? 'min-w-0 w-full' : 'min-w-0 flex-1'}>
            {primary ? <a href={primary} target="_blank" rel="noopener noreferrer" className="text-sm font-semibold leading-relaxed text-text-primary hover:text-brand-blue-light">{report.title}</a>
              : <p className="text-sm font-semibold text-text-primary">{report.title}</p>}
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-text-secondary">
              <time>{report.publish_date}</time><span>{report.org_name || '机构未提供'}</span>
              {report.rating && <span>{report.rating}</span>}
              {!compact && <><span>{report.report_type === 'company' ? '公司研报' : report.report_type === 'industry' ? '行业研报' : '待分类'}</span>
                {report.industry_name && <span>{report.industry_name}</span>}
                {report.symbol && /^\d{6}$/.test(report.symbol) && <Link className="text-brand-blue-light" href={researchStockHref(report.symbol, returnTo, asOfDate)}>{report.stock_name || report.symbol} {report.stock_name ? report.symbol : ''}</Link>}
              </>}
            </div>
          </div>
          <div className={compact ? 'flex w-full flex-wrap gap-4 text-xs text-brand-blue-light' : 'flex shrink-0 gap-3 text-xs text-brand-blue-light'}>
            <button type="button" aria-expanded={selectedId === report.id} onClick={() => setSelectedId(selectedId === report.id ? null : report.id)}>{selectedId === report.id ? '收起' : '详情'}</button>
            {original && <a href={original} target="_blank" rel="noopener noreferrer">原文 ↗</a>}
            {pdf && <a href={pdf} target="_blank" rel="noopener noreferrer">PDF ↗</a>}
            {!primary && <span className="text-text-secondary">暂无原文链接</span>}
          </div>
        </div>
        {selectedId === report.id && <div className="mt-3 rounded-lg border border-border-primary bg-bg-secondary p-3 text-xs text-text-secondary">
          {detail.isLoading ? <p role="status">正在读取详情…</p> : detail.isError ? <p role="alert">详情读取失败。<button type="button" className="ml-2 underline" onClick={() => void detail.refetch()}>重试</button></p> : detail.data?.id === report.id ? <dl className="grid gap-2 sm:grid-cols-2">
            <div><dt>研报标题</dt><dd className="mt-1 text-text-primary">{detail.data.title}</dd></div>
            <div><dt>研究员</dt><dd>{detail.data.researcher || '未提供'}</dd></div>
            <div><dt>页数</dt><dd>{detail.data.pages || '未提供'}</dd></div>
            <div><dt>来源</dt><dd>{detail.data.source} · {detail.data.source_report_id}</dd></div>
            <div><dt>入库时间（北京时间）</dt><dd>{formatResearchTime(detail.data.imported_at)}</dd></div>
          </dl> : null}
          <p className="mt-3">本页展示来源元数据；点击原文或 PDF 阅读完整研报。</p>
        </div>}
      </li>;
    })}
  </ul>;
}
