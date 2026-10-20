import { buildChanHistory } from './stockChartChanHistory';
import { CHAN_MAX_INPUT_BARS, type ChanVariant } from './stockChartChanOverlay';

interface HistoryRequest {
    id: number;
    dates: string[];
    candles: unknown[][];
    variant?: ChanVariant;
    auditSignals?: boolean;
    includeZones?: boolean;
}
interface HistoryWorkerScope {
    onmessage: ((event: MessageEvent<unknown>) => void) | null;
    postMessage: (message: unknown) => void;
}
const workerScope = self as unknown as HistoryWorkerScope;
workerScope.onmessage = (event) => {
    const input = event.data as Partial<HistoryRequest> | null;
    const id = input?.id;
    try {
        if (!input || !Number.isSafeInteger(id) || !Array.isArray(input.dates)
            || !Array.isArray(input.candles) || input.dates.length > CHAN_MAX_INPUT_BARS
            || input.dates.length !== input.candles.length
            || (input.auditSignals !== undefined && typeof input.auditSignals !== 'boolean')
            || (input.includeZones !== undefined && typeof input.includeZones !== 'boolean')
            || (input.variant !== undefined && input.variant !== 'xueqiu' && input.variant !== 'zhihu')) {
            throw new Error('历史中枢请求参数无效');
        }
        const result = buildChanHistory(input.dates, input.candles, input.variant,
            { auditSignals: input.auditSignals, includeZones: input.includeZones });
        if (input.dates.length && !result.barCount) throw new Error('历史中枢行情数据无效');
        workerScope.postMessage({ id, status: 'success', result });
    } catch (error) {
        workerScope.postMessage({ id, status: 'error',
            message: error instanceof Error ? error.message : '历史中枢计算失败' });
    }
};
