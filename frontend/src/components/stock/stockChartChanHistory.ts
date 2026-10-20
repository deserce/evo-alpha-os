import {
    buildChanOverlay, CHAN_MAX_BARS, CHAN_MAX_INPUT_BARS,
    type ChanVariant, type ChanZone, type ChanPoint,
} from './stockChartChanOverlay';

export const CHAN_HISTORY_MAX_ZONES = 64;
export const CHAN_HISTORY_MAX_REVOKED_SIGNALS = 128;
export interface ChanRevokedSignal {
    index: number;
    price: number;
    label: string;
    kind: 'buy' | 'sell';
    firstSeen: string;
    revokedAt: string;
    episodeCount: number;
}
export interface ChanHistoryOptions {
    auditSignals?: boolean;
    includeZones?: boolean;
}
export interface ChanHistory {
    zones: ChanZone[];
    revokedSignals: ChanRevokedSignal[];
    revokedSignalCount: number;
    activeSignalCount: number;
    auditStartDate: string | null;
    auditEndDate: string | null;
    elapsedMs: number;
    barCount: number;
    truncated: boolean;
}

/** Replay the exact formula at each completed prefix; this is a visual extension only. */
export function buildChanHistory(
    dates: readonly string[], candles: readonly (readonly unknown[])[],
    variant: ChanVariant = 'xueqiu', options: ChanHistoryOptions = {},
): ChanHistory {
    const started = performance.now();
    const result: ChanHistory = { zones: [], revokedSignals: [], revokedSignalCount: 0, activeSignalCount: 0,
        auditStartDate: null, auditEndDate: null, elapsedMs: 0, barCount: 0, truncated: false };
    const finish = () => {
        result.elapsedMs = performance.now() - started;
        return result;
    };
    if (!Array.isArray(dates) || !Array.isArray(candles)
        || dates.length > CHAN_MAX_INPUT_BARS || dates.length !== candles.length
        || (variant !== 'xueqiu' && variant !== 'zhihu')) return finish();
    const validated = buildChanOverlay(dates, candles, variant);
    result.barCount = validated.barCount;
    result.truncated = validated.truncated;
    if (!validated.barCount) return finish();
    const offset = Math.max(0, dates.length - CHAN_MAX_BARS);
    const selectedDates = dates.slice(offset);
    const selectedCandles = candles.slice(offset);
    result.auditStartDate = selectedDates[0] ?? null;
    result.auditEndDate = selectedDates.at(-1) ?? null;
    const active = new Map<string, { point: ChanPoint; firstSeen: string }>();
    const revoked = new Map<string, ChanRevokedSignal>();
    const episodes = new Map<string, number>();
    const structures = new Map<string, ChanZone>();
    for (let end = 1; end <= selectedDates.length; end++) {
        const snapshot = buildChanOverlay(
            selectedDates.slice(0, end), selectedCandles.slice(0, end), variant,
        );
        if (options.auditSignals) {
            const signalSnapshot = variant === 'zhihu' ? snapshot : buildChanOverlay(
                selectedDates.slice(0, end), selectedCandles.slice(0, end), 'zhihu',
            );
            const current = new Map(signalSnapshot.points.filter(p => p.kind !== 'pivot')
                .map(p => [`${p.index}:${p.kind}:${p.label}`, p]));
            for (const [key, previous] of active) {
                if (current.has(key)) continue;
                active.delete(key);
                const point = previous.point;
                const episodeCount = (episodes.get(key) ?? 0) + 1;
                episodes.set(key, episodeCount);
                result.revokedSignalCount++;
                revoked.delete(key);
                revoked.set(key, { index: point.index + offset, price: point.price,
                    label: point.label, kind: point.kind as 'buy' | 'sell',
                    firstSeen: previous.firstSeen, revokedAt: selectedDates[end - 1], episodeCount });
                if (revoked.size > CHAN_HISTORY_MAX_REVOKED_SIGNALS) {
                    const oldest = revoked.keys().next().value;
                    if (oldest !== undefined) revoked.delete(oldest);
                }
            }
            for (const [key, point] of current) {
                // A reappearing source mark owns the position; never overlay its revoked circle.
                revoked.delete(key);
                if (!active.has(key)) active.set(key, { point, firstSeen: selectedDates[end - 1] });
            }
        }
        for (const zone of options.includeZones === false ? [] : snapshot.zones) {
            const state = zone.dashedTop || zone.dashedBottom ? 'forming' : 'confirmed';
            const key = `${zone.startIndex}:${zone.type}:${state}`;
            // Replacement moves the latest snapshot to the end of the bounded history.
            structures.delete(key);
            structures.set(key, { ...zone,
                startIndex: zone.startIndex + offset, endIndex: zone.endIndex + offset });
            if (structures.size > CHAN_HISTORY_MAX_ZONES) {
                const oldest = structures.keys().next().value;
                if (oldest !== undefined) structures.delete(oldest);
            }
        }
    }
    result.revokedSignals = [...revoked.values()];
    result.activeSignalCount = active.size;
    result.zones = [...structures.values()];
    return finish();
}
