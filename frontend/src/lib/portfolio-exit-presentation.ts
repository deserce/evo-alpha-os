export function cExitPresentation(eventType: string, payloadJson: string) {
  if (!['TREND_EXIT_WATCH', 'TREND_EXIT_TRIGGERED'].includes(eventType)) return null;
  try {
    const payload: unknown = JSON.parse(payloadJson);
    if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return null;
    const fields = payload as Record<string, unknown>;
    if (fields.strategy !== 'C') return null;
    return {
      label: eventType === 'TREND_EXIT_TRIGGERED' ? 'C 结构退出' : 'C 风险预警',
      reason: typeof fields.reason === 'string' ? fields.reason : '请核对策略 C 结构条件',
      structureNote: fields.stop_binding === 'BOUND_AT_ENTRY'
        ? '结构失效线已绑定（前复权）'
        : '结构失效线未绑定',
    };
  } catch {
    return null;
  }
}
