export type ReportJobStatus = 'NOT_GENERATED' | 'RUNNING' | 'BUSY' | 'PARTIAL' | 'FAILED' | 'STALE' | 'COMPLETED';
export type ReportJob = {
  date: string; status: ReportJobStatus; stage: string; completed: number; total: number; reason: string;
};
export type AutoGenerationState = { date: string; attempted: boolean };

export function advanceAutoGeneration(previous: AutoGenerationState, date: string,
  status: ReportJobStatus | undefined, open: boolean, pending: boolean): { state: AutoGenerationState; start: boolean } {
  const state = previous.date === date ? { ...previous } : { date, attempted: false };
  if (status === 'BUSY') state.attempted = false;
  const start = Boolean(open && date && !pending && status === 'NOT_GENERATED' && !state.attempted);
  if (start) state.attempted = true;
  return { state, start };
}

export function shouldPollReport(open: boolean, status: ReportJobStatus | undefined): boolean {
  return open && (status === 'RUNNING' || status === 'BUSY');
}

export function parseReportJob(value: unknown, expectedDate: string): ReportJob {
  if (!value || typeof value !== 'object') throw new Error('日报任务响应不完整');
  const job = value as Record<string, unknown>;
  const statuses = ['NOT_GENERATED', 'RUNNING', 'BUSY', 'PARTIAL', 'FAILED', 'STALE', 'COMPLETED'];
  if (job.date !== expectedDate || typeof job.status !== 'string' || !statuses.includes(job.status)
    || typeof job.stage !== 'string' || typeof job.reason !== 'string'
    || typeof job.completed !== 'number' || !Number.isFinite(job.completed) || job.completed < 0
    || typeof job.total !== 'number' || !Number.isFinite(job.total) || job.total < 0) {
    throw new Error('日报任务日期或状态无效');
  }
  return job as ReportJob;
}

export function summarizeMissing(messages: string[]): { reason: string; items: string[] }[] {
  const grouped = new Map<string, string[]>();
  for (const message of new Set(messages)) {
    const reason = message.replace(/^\d{6} \d{4}-\d{2}-\d{2}：/, '');
    const items = grouped.get(reason) ?? [];
    items.push(message); grouped.set(reason, items);
  }
  return [...grouped].map(([reason, items]) => ({ reason, items }));
}

export function formatArchiveTime(value: string): string {
  const instant = new Date(value);
  if (!Number.isFinite(instant.getTime())) return '时间未知';
  return instant.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hourCycle: 'h23' });
}
