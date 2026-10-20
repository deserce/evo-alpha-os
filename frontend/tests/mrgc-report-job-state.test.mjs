import test from 'node:test';
import assert from 'node:assert/strict';
import { advanceAutoGeneration, shouldPollReport, parseReportJob, summarizeMissing, formatArchiveTime } from '../src/lib/mrgcReportJobState.ts';

test('only a visible not-generated date starts once; a date change resets bounded state', () => {
  let state = { date: '', attempted: false };
  let next = advanceAutoGeneration(state, '2026-09-29', 'NOT_GENERATED', true, false);
  assert.equal(next.start, true); state = next.state;
  assert.equal(advanceAutoGeneration(state, '2026-09-29', 'NOT_GENERATED', true, false).start, false);
  assert.equal(advanceAutoGeneration(state, '2026-09-30', 'NOT_GENERATED', true, false).start, true);
  assert.equal(advanceAutoGeneration(state, '2026-09-30', 'NOT_GENERATED', false, false).start, false);
});

test('busy never starts; once other worker ends, ungenerated date can start', () => {
  const busy = advanceAutoGeneration({ date: '2026-09-29', attempted: true }, '2026-09-29', 'BUSY', true, false);
  assert.equal(busy.start, false);
  assert.equal(advanceAutoGeneration(busy.state, '2026-09-29', 'NOT_GENERATED', true, false).start, true);
  for (const status of ['PARTIAL', 'FAILED', 'STALE', 'COMPLETED', 'RUNNING']) {
    assert.equal(advanceAutoGeneration(busy.state, '2026-09-29', status, true, false).start, false);
  }
});

test('poll only opened running or busy tasks, and validate exact-date job responses', () => {
  assert.equal(shouldPollReport(true, 'RUNNING'), true);
  assert.equal(shouldPollReport(true, 'BUSY'), true);
  assert.equal(shouldPollReport(false, 'RUNNING'), false);
  assert.equal(shouldPollReport(true, 'PARTIAL'), false);
  const job = { date: '2026-09-29', status: 'NOT_GENERATED', stage: 'NOT_GENERATED', completed: 0, total: 0, reason: '未生成' };
  assert.deepEqual(parseReportJob(job, job.date), job);
  assert.throws(() => parseReportJob(job, '2026-09-30'));
  assert.throws(() => parseReportJob({ ...job, status: 'invalid' }, job.date));
});

test('missing issues deduplicate and collapse stock-specific errors by reason', () => {
  const rows = ['600001 2026-09-29：C 尚未计算', '600002 2026-09-29：C 尚未计算', '600001 2026-09-29：C 尚未计算', '公司：分组不存在'];
  assert.deepEqual(summarizeMissing(rows), [
    { reason: 'C 尚未计算', items: rows.slice(0, 2) }, { reason: '公司：分组不存在', items: [rows[3]] },
  ]);
});

test('archive time displays Beijing time with date rollover and invalid input fallback', () => {
  assert.equal(formatArchiveTime('2026-10-03T14:35:58+00:00'), '2026/10/3 22:35:58');
  assert.equal(formatArchiveTime('2026-10-03T16:00:00Z'), '2026/10/4 00:00:00');
  assert.equal(formatArchiveTime('not-a-date'), '时间未知');
});
