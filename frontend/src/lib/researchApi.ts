import { api } from '@/lib/api';

export interface ResearchReport {
  id: string;
  source: string;
  source_report_id: string;
  title: string;
  report_type: 'company' | 'industry' | 'unknown';
  symbol: string | null;
  stock_name: string | null;
  industry_name: string | null;
  org_name: string | null;
  rating: string | null;
  researcher: string | null;
  publish_date: string;
  pdf_url: string | null;
  original_url: string | null;
  pages: number | null;
  imported_at: string;
}
export interface ResearchFilters {
  q?: string;
  symbol?: string;
  report_type?: '' | ResearchReport['report_type'];
  industry?: string;
  org?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}
export interface ResearchPageResult {
  status: 'ready' | 'not_imported';
  items: ResearchReport[];
  total: number;
  page: number;
  page_size: number;
}
export interface ResearchSyncStatus {
  status: 'never' | 'running' | 'success' | 'failed' | 'partial';
  last_attempt_at: string | null;
  last_success_at: string | null;
  coverage_through: string | null;
  inserted: number;
  updated: number;
  error: string | null;
  schedule: string;
}
export const researchApi = {
  reports: (params: ResearchFilters, signal?: AbortSignal): Promise<ResearchPageResult> =>
    api.get('/v1/research/reports', { params, signal, timeout: 15000 }),
  facets: (signal?: AbortSignal): Promise<{ status: 'ready' | 'not_imported'; industries: string[]; organizations: string[]; sync?: ResearchSyncStatus }> =>
    api.get('/v1/research/facets', { signal, timeout: 15000 }),
  report: (id: string, signal?: AbortSignal): Promise<ResearchReport> =>
    api.get(`/v1/research/reports/${encodeURIComponent(id)}`, { signal, timeout: 15000 }),
};
export const researchKeys = {
  reports: (filters: ResearchFilters) => ['researchReports', {
    ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '' && value !== undefined)),
    page: filters.page || 1, page_size: filters.page_size || 30,
  }] as const,
  facets: ['researchFacets'] as const,
};
