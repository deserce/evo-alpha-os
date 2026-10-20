/** Fixed translations of the two supplied TDX formulas, not a general formula interpreter.
 * BACKSET/DRAWLINE/ZIG repaint history; CONST-based zones describe only the latest window.
 * Bounded to 520 bars and used exclusively for chart display, never trading decisions.
 */
export const CHAN_MAX_BARS = 520;
export const CHAN_MAX_INPUT_BARS = 4096;
export const CHAN_OVERLAY_NOTICE = '原公式含未来函数，历史线段与买卖标记可能重绘；仅供图形参考。区间仅显示当前结构。';
export type ChanVariant = 'xueqiu' | 'zhihu';
export interface ChanLine {
    name: string;
    color: string;
    dashed: boolean;
    width: number;
    lineType: 'solid' | 'dashed' | 'dotted';
    data: (number | null)[];
}
export interface ChanPoint {
    index: number;
    price: number;
    label: string;
    kind: 'pivot' | 'buy' | 'sell';
    direction?: 'high' | 'low';
}
export type ChanZoneType = 'expandingA' | 'expandingB' | 'convergingA' | 'convergingB';
export interface ChanZone {
    type: ChanZoneType;
    startIndex: number;
    endIndex: number;
    low: number;
    high: number;
    color: string;
    dashedTop: boolean;
    dashedBottom: boolean;
}
export interface ChanOverlay {
    lines: ChanLine[];
    points: ChanPoint[];
    zones: ChanZone[];
    zoneStates: ChanZoneState[];
    repainting: true;
    barCount: number;
    truncated: boolean;
}
type Series = number[];
const missing = Number.NaN;

// REF uses the earliest available value when the requested positive offset precedes history.
export function tdxRef(a: Series, i: number, offset = 1): number {
    if (!Number.isFinite(offset) || offset < 0) return missing;
    return a[Math.max(0, i - Math.floor(offset))] ?? missing;
}
const ref = tdxRef;
function extrema(a: Series, i: number, window: number, high: boolean): number {
    if (!Number.isFinite(window) || window < 0) return missing;
    const start = window === 0 ? 0 : Math.max(0, i - Math.floor(window) + 1);
    let value = high ? -Infinity : Infinity;
    for (let j = start; j <= i; j++) {
        if (!Number.isFinite(a[j])) return missing;
        value = high ? Math.max(value, a[j]) : Math.min(value, a[j]);
    }
    return Number.isFinite(value) ? value : missing;
}
function barsLast(a: Series): Series {
    let last = -1;
    return a.map((value, i) => {
        if (Number.isFinite(value) && value !== 0) last = i;
        // Compatibility boundary: no condition in loaded history is deliberately invalid.
        // TDX's implicit startup value has not been verified against an exported numeric fixture.
        return last < 0 ? missing : i - last;
    });
}
export function tdxBackset(a: Series, width: number): Series {
    if (!Number.isInteger(width) || width <= 0 || a.length > CHAN_MAX_BARS) return [];
    const result = a.map(() => 0);
    a.forEach((value, i) => {
        if (Number.isFinite(value) && value !== 0) {
            for (let j = Math.max(0, i - width + 1); j <= i; j++) result[j] = 1;
        }
    });
    return result;
}
const backset = tdxBackset;
function range(a: Series, high: boolean): Series {
    return a.map((value, i) => {
        if (!Number.isFinite(value)) return missing;
        let j = i - 1;
        while (j >= 0 && Number.isFinite(a[j]) && (high ? a[j] <= value : a[j] >= value)) j--;
        return i - j;
    });
}
export function tdxDrawline(start: Series, startPrice: Series, end: Series, endPrice: Series): Series {
    if (start.length > CHAN_MAX_BARS || [startPrice, end, endPrice].some(a => a.length !== start.length)) return [];
    const result = start.map(() => missing);
    let from = -1;
    for (let i = 0; i < start.length; i++) {
        if (start[i] && Number.isFinite(startPrice[i])) from = i;
        if (end[i] && from >= 0 && from < i && Number.isFinite(endPrice[i])) {
            for (let j = from; j <= i; j++) {
                result[j] = startPrice[from] + (endPrice[i] - startPrice[from]) * (j - from) / (i - from);
            }
            // Preserve original endpoint prices: C1=C2 in the source requires exact equality.
            result[from] = startPrice[from];
            result[i] = endPrice[i];
            from = -1;
        }
    }
    return result;
}
const drawline = tdxDrawline;

/** Closing-price percentage ZIG. The unresolved final leg is intentionally provisional. */
export function tdxZig(close: readonly number[], percent: number): Series {
    if (!Number.isFinite(percent) || percent <= 0 || close.length > CHAN_MAX_BARS) return [];
    const result = close.map(() => missing);
    if (!close.length || close.some((value) => !Number.isFinite(value) || value <= 0)) return result;
    const pivots: { index: number; price: number }[] = [{ index: 0, price: close[0] }];
    let direction = 0;
    let candidate = 0;
    let initialHigh = 0;
    let initialLow = 0;
    const threshold = percent / 100;
    for (let i = 1; i < close.length; i++) {
        if (direction === 0) {
            if (close[i] >= close[initialHigh]) initialHigh = i;
            if (close[i] <= close[initialLow]) initialLow = i;
            if (close[i] >= close[initialLow] * (1 + threshold)) {
                direction = 1;
                if (initialLow > 0) pivots.push({ index: initialLow, price: close[initialLow] });
            } else if (close[i] <= close[initialHigh] * (1 - threshold)) {
                direction = -1;
                if (initialHigh > 0) pivots.push({ index: initialHigh, price: close[initialHigh] });
            }
            if (direction) candidate = i;
        } else if (direction > 0) {
            if (close[i] >= close[candidate]) candidate = i;
            else if (close[i] <= close[candidate] * (1 - threshold)) {
                pivots.push({ index: candidate, price: close[candidate] });
                direction = -1;
                candidate = i;
            }
        } else {
            if (close[i] <= close[candidate]) candidate = i;
            else if (close[i] >= close[candidate] * (1 + threshold)) {
                pivots.push({ index: candidate, price: close[candidate] });
                direction = 1;
                candidate = i;
            }
        }
    }
    if (candidate > pivots[pivots.length - 1].index) {
        pivots.push({ index: candidate, price: close[candidate] });
    }
    const last = close.length - 1;
    if (pivots[pivots.length - 1].index !== last) pivots.push({ index: last, price: close[last] });
    if (pivots.length === 1) result[0] = close[0];
    for (let p = 1; p < pivots.length; p++) {
        const a = pivots[p - 1], b = pivots[p];
        for (let i = a.index; i <= b.index; i++) {
            result[i] = a.price + (b.price - a.price) * (i - a.index) / (b.index - a.index);
        }
    }
    return result;
}

/** COUNT with a per-bar window; zero counts all loaded history, without startup inference. */
export function tdxCount(values: Series, index: number, width: number): number {
    if (!Number.isFinite(width) || width < 0 || !Number.isInteger(index)
        || index < 0 || index >= values.length || values.length > CHAN_MAX_BARS) return missing;
    let sum = 0;
    const start = width === 0 ? 0 : Math.max(0, index - Math.floor(width) + 1);
    for (let i = start; i <= index; i++) {
        if (Number.isFinite(values[i]) && values[i] !== 0) sum++;
    }
    return sum;
}

/** Translate the shared original low/high retention passes, including asymmetric XA handling. */
function retainedPivots(high: Series, low: Series): {
    signal: Series; highSeed: Series; lowSeed: Series;
} {
    const map = (fn: (i: number) => number): Series => high.map((_, i) => fn(i));
    const hh = (a: Series, i: number, n: number) => extrema(a, i, n, true);
    const ll = (a: Series, i: number, n: number) => extrema(a, i, n, false);
    const rollingHigh5 = map(i => hh(high, i, 5));
    const rollingHigh4 = map(i => hh(high, i, 4));
    const rollingLow5 = map(i => ll(low, i, 5));
    const rollingLow4 = map(i => ll(low, i, 4));
    const lowSeed = backset(map(i => +(rollingLow5[i] < ref(rollingLow4, i))), 4);
    const highSeed = backset(map(i => +(rollingHigh5[i] > ref(rollingHigh4, i))), 4);
    const lowB = backset(map(i => +(lowSeed[i] === 0 && ref(lowSeed, i) === 1)), 2);
    const highB = backset(map(i => +(highSeed[i] === 0 && ref(highSeed, i) === 1)), 2);
    const lc = map(i => lowB[i] === 1 && ref(lowB, i) === 0 ? -1 : 0);
    const hc = map(i => +(highB[i] === 1 && ref(highB, i) === 0));
    const gap = map(i => low[i] > ref(high, i) ? 1 : high[i] < ref(low, i) ? -1 : 0);
    const lowRange = range(low, false), highRange = range(high, true);
    let ht = barsLast(hc), lt = barsLast(lc);
    const initialA = map(i => lc[i] === -1 && ref(ht, i) > ref(lt, i)
        && ll(low, i, ht[i] + 1) < ll(low, Math.max(0, i - 1), ref(ht, i) + 1) ? -1 : 0);
    const initialB = map(i => lc[i] === -1 && ref(ht, i) <= ref(lt, i)
        && (ht[i] >= 4 || ll(gap, i, ht[i]) === -1
            || ll(low, i, lt[i] + 2) < ll(low, Math.max(0, i - 1), ref(lt, i) + 1)) ? -1 : 0);
    let retainedLow = map(i => (initialA[i] === -1 || initialB[i] === -1)
        && low[i] < ref(high, i, ht[i] + 1) ? -1 : 0);
    let retainedHigh: Series = hc.map(() => 0);
    // The source has three full passes: base, X, YX. X/YX use high-A for the low precheck.
    for (let pass = 0; pass < 3; pass++) {
        const previousLow = retainedLow;
        const preHigh = map(i => (lt[i] < 4 && hh(gap, i, lt[i]) !== 1)
            || ref(previousLow, i, lt[i]) === 0 ? 1 : 0);
        const checkHigh = map(i => hc[i] === 1 && ref(lt, i) <= ref(ht, i) && preHigh[i] === 1
            && highRange[i] > ref(lowRange, i, lt[i] + 1)
            && highRange[i] > ref(lowRange, i, lt[i])
            && highRange[i] > ref(highRange, i, ht[i]) ? 1 : 0);
        const highA = map(i => hc[i] === 1 && ref(lt, i) > ref(ht, i)
            && hh(high, i, lt[i] + 1) > hh(high, Math.max(0, i - 1), ref(lt, i) + 1) ? 1 : 0);
        const highBPass = map(i => hc[i] === 1 && ref(lt, i) <= ref(ht, i)
            && ref(previousLow, i, lt[i]) === -1
            && (lt[i] >= 4 || hh(gap, i, lt[i]) === 1) ? 1 : 0);
        retainedHigh = map(i => (highA[i] || highBPass[i] || checkHigh[i])
            && high[i] > ref(low, i, lt[i] + 1) ? 1 : 0);
        const lowPreSource = pass === 0 ? retainedHigh : highA;
        const preLow = map(i => (ht[i] < 4 && hh(gap, i, ht[i]) !== 1)
            || ref(lowPreSource, i, ht[i]) === 0 ? 1 : 0);
        const checkLow = map(i => lc[i] === -1 && ref(ht, i) <= ref(lt, i) && preLow[i] === 1
            && lowRange[i] > ref(highRange, i, ht[i] + 1)
            && lowRange[i] > ref(highRange, i, ht[i])
            && lowRange[i] > ref(lowRange, i, lt[i]) ? -1 : 0);
        const lowA = map(i => lc[i] === -1 && ref(ht, i) > ref(lt, i)
            && ll(low, i, ht[i] + 1) < ll(low, Math.max(0, i - 1), ref(ht, i) + 1) ? -1 : 0);
        const lowBPass = map(i => lc[i] === -1 && ref(ht, i) <= ref(lt, i)
            && (ht[i] >= 4 || ll(gap, i, ht[i]) === -1 || checkLow[i] === -1) ? -1 : 0);
        retainedLow = map(i => (lowA[i] === -1 || lowBPass[i] === -1)
            && low[i] < ref(high, i, ht[i] + 1) ? -1 : 0);
        if (pass < 2) {
            ht = barsLast(retainedHigh);
            lt = barsLast(retainedLow);
        }
    }
    const signal = map(i => {
        if (retainedHigh[i] === 1 && retainedLow[i] === -1) {
            if (high[i] > ref(high, i, ref(ht, i) + 2)) return 1;
            if (low[i] < ref(low, i, ref(lt, i) + 2)) return -1;
        }
        return retainedHigh[i] + retainedLow[i];
    });
    return { signal, highSeed, lowSeed };
}

export interface ChanZoneState {
    type: ChanZoneType;
    historyValid: boolean;
    conditionMet: boolean;
    rendered: boolean;
}
export interface ChanCurrentZoneInput {
    a1: number; a2: number; da1: number; da2: number;
    h1: number; h2: number; l1: number; l2: number;
    close: number; lastIndex: number; offset?: number;
}

/** Four current CONST zones, translating the source inequalities without tolerance or relaxation. */
export function calculateChanCurrentZones(input: ChanCurrentZoneInput): {
    zones: ChanZone[]; states: ChanZoneState[];
} {
    const { a1, a2, da1, da2, h1, h2, l1, l2, close: c, lastIndex: last,
        offset = 0 } = input;
    const zones: ChanZone[] = [];
    const states: ChanZoneState[] = [];
    const valid = [a1, a2, da1, da2, h1, h2, l1, l2, c, last, offset].every(Number.isFinite)
        && [a1, a2, da1, da2, last, offset].every(value => Number.isInteger(value) && value >= 0)
        && Math.max(a2, da2, a1, da1) <= last;
    const zone = (type: ChanZoneType, condition: boolean, startAgo: number,
        lower: number, upper: number, color: string, dashedTop = false, dashedBottom = false) => {
        const rendered = valid && condition && Number.isFinite(lower)
            && Number.isFinite(upper) && upper >= lower;
        states.push({ type, historyValid: valid, conditionMet: valid && condition, rendered });
        if (rendered) zones.push({ type, startIndex: last - startAgo + offset,
            endIndex: last + offset, low: lower, high: upper, color, dashedTop, dashedBottom });
    };
    const expandingA = l1 < l2 && h2 > h1 && da2 > a1 && a1 > da1 && a2 > da2 && c > l2;
    zone('expandingA', expandingA, da2, c < h1 ? l2 : l1, c < h1 ? c : h1, '#d946ef', c < h1);
    const expandingB = l1 > l2 && h1 > h2 && da1 > a1 && a2 > da1 && da2 > a2 && c < h2;
    zone('expandingB', expandingB, a2, c > l1 ? c : l1, h2, '#22c55e', false, c > l1);
    const convergingA = l1 > l2 && h2 > h1 && da2 > a1 && a1 > da1 && a2 > da2 && c > l1;
    zone('convergingA', convergingA, da2, l1, c < h1 ? c : h1, '#eab308', c < h1);
    const convergingB = l1 > l2 && h1 < h2 && da1 > a1 && a2 > da1 && da2 > a2 && c < h1;
    zone('convergingB', convergingB, a2, c > l1 ? c : l1, h1, '#22d3ee', false, c > l1);
    return { zones, states };
}

/** Build an optional chart overlay without requests, database writes, or background work. */
export function buildChanOverlay(
    dates: readonly string[], candles: readonly (readonly unknown[])[], variant: ChanVariant,
): ChanOverlay {
    const output: ChanOverlay = {
        lines: [], points: [], zones: [], zoneStates: [], repainting: true, barCount: 0,
        truncated: dates.length > CHAN_MAX_BARS,
    };
    if ((variant !== 'zhihu' && variant !== 'xueqiu') || !dates.length
        || dates.length > CHAN_MAX_INPUT_BARS || dates.length !== candles.length) return output;
    const offset = Math.max(0, dates.length - CHAN_MAX_BARS);
    const selected = candles.slice(offset);
    // Reject an invalid window rather than bridging missing bars or coercing null to a zero price.
    if (selected.some((bar, i) => !Array.isArray(bar) || bar.length < 4
        || typeof dates[offset + i] !== 'string' || !dates[offset + i]
        || bar.slice(0, 4).some(value => typeof value !== 'number' || !Number.isFinite(value) || value <= 0)
        || (bar[2] as number) > Math.min(bar[0] as number, bar[1] as number)
        || (bar[3] as number) < Math.max(bar[0] as number, bar[1] as number)
        || (i > 0 && dates[offset + i] <= dates[offset + i - 1]))) return output;
    const high = selected.map(bar => bar[3] as number);
    const low = selected.map(bar => bar[2] as number);
    const close = selected.map(bar => bar[1] as number);
    const n = high.length;
    output.barCount = n;
    const map = (fn: (i: number) => number): Series => high.map((_, i) => fn(i));
    const addLine = (name: string, color: string, dashed: boolean, data: Series,
        width = 1.6, lineType: ChanLine['lineType'] = dashed ? 'dashed' : 'solid') => {
        output.lines.push({ name, color, dashed, width, lineType,
            data: [...Array<null>(offset).fill(null), ...data.map(v => Number.isFinite(v) ? v : null)] });
    };
    const { signal, highSeed, lowSeed } = retainedPivots(high, low);
    const highs = map(i => +(signal[i] === 1)), lows = map(i => +(signal[i] === -1));
    const price = map(i => signal[i] === 1 ? high[i] : signal[i] === -1 ? low[i] : missing);
    const c1 = drawline(lows, price, highs, price), c2 = drawline(highs, price, lows, price);
    addLine('缠论上行笔', variant === 'zhihu' ? '#ffffff' : '#94a3b8', variant === 'zhihu', c1,
        variant === 'zhihu' ? 1 : 1.6, variant === 'zhihu' ? 'dotted' : 'solid');
    addLine('缠论下行笔', variant === 'zhihu' ? '#ffffff' : '#94a3b8', variant === 'zhihu', c2,
        variant === 'zhihu' ? 1 : 1.6, variant === 'zhihu' ? 'dotted' : 'solid');
    // CC1/CC2 preserve the different source parameters: 4*5 versus 11*5 bars.
    const window = variant === 'zhihu' ? 55 : 20;
    const rh = map(i => extrema(high, i, window, true));
    const rl = map(i => extrema(low, i, window, false));
    const a = map(i => +(high[i] === rh[i] && rh[i] > ref(rh, i)));
    const b = map(i => +(low[i] === rl[i] && rl[i] < ref(rl, i)));
    addLine('缠论大级别下行', variant === 'zhihu' ? '#ff0000' : '#ef4444', false, drawline(a, high, b, low), variant === 'zhihu' ? 2 : 1.6);
    addLine('缠论大级别上行', variant === 'zhihu' ? '#ff0000' : '#ef4444', false, drawline(b, low, a, high), variant === 'zhihu' ? 2 : 1.6);
    for (let i = 0; i < n; i++) {
        if (signal[i]) output.points.push({ index: i + offset, price: price[i],
            label: variant === 'zhihu' ? signal[i] > 0 ? '卖' : '买' : '', kind: 'pivot',
            direction: signal[i] > 0 ? 'high' : 'low' });
    }
    // N:=(0,1,1) is an exported parameter tuple. Its default is 1, enabling current zones.
    // The PDFs' C2=C1 equality is checked only at real retained endpoints.
    const top = map(i => +(Number.isFinite(c1[i]) && c1[i] === c2[i] && highSeed[i] && signal[i] === 1));
    const bottom = map(i => +(Number.isFinite(c1[i]) && c1[i] === c2[i] && lowSeed[i] && signal[i] === -1));
    const ht = barsLast(top), lt = barsLast(bottom);
    const last = n - 1;
    const a1 = ht[last], da1 = lt[last];
    const a2 = ref(ht, last, a1 + 1) + a1 + 1;
    const da2 = ref(lt, last, da1 + 1) + da1 + 1;
    const h1 = ref(high, last, a1), h2 = ref(high, last, a2);
    const l1 = ref(low, last, da1), l2 = ref(low, last, da2), c = close[last];
    const currentZones = calculateChanCurrentZones({ a1, a2, da1, da2, h1, h2, l1, l2,
        close: c, lastIndex: last, offset });
    const zhihuColors: Record<ChanZoneType, string> = { expandingA: '#ff80ff',
        expandingB: '#00ff00', convergingA: '#ffff00', convergingB: '#80ffff' };
    output.zones = currentZones.zones.map(zone => variant === 'zhihu'
        ? { ...zone, color: zhihuColors[zone.type] } : zone);
    output.zoneStates = currentZones.states;
    if (variant === 'zhihu') {
        const p10 = tdxZig(close, 10), p20 = tdxZig(close, 20);
        const up = (p: Series) => map(i => +(p[i] > ref(p, i)
            && ref(p, i) <= ref(p, i, 2) && ref(p, i, 2) <= ref(p, i, 3)));
        const down = (p: Series) => map(i => +(p[i] < ref(p, i)
            && ref(p, i) >= ref(p, i, 2) && ref(p, i, 2) >= ref(p, i, 3)));
        const en2 = up(p10), ex2 = down(p10), en3 = up(p20);
        const strong3 = barsLast(map(i => +(p20[i] < ref(p20, i))));
        const weak3 = barsLast(map(i => +(p20[i] > ref(p20, i))));
        const count = tdxCount;
        for (let i = 0; i < n; i++) {
            const rising = p20[i] > ref(p20, i), falling = p20[i] < ref(p20, i);
            const ztBuy = rising ? count(en2, i, strong3[i]) : 0;
            const ztSell = rising ? count(ex2, i, strong3[i]) : 0;
            const dtBuy = falling ? count(en2, i, weak3[i]) : 0;
            const point = (condition: boolean, label: string, kind: 'buy' | 'sell') => {
                if (condition) output.points.push({ index: i + offset,
                    price: kind === 'buy' ? low[i] * 0.97 : high[i] * 1.03, label, kind });
            };
            point(!!en2[i] && dtBuy === 1, '[类 2 买]', 'buy');
            point(!!en3[i] && ztBuy === 1, '[1 买]', 'buy');
            point(!!ex2[i] && rising && ztSell === 1, '[2 卖]', 'sell');
            point(!!en2[i] && rising && ztBuy === 2, '[2 买]', 'buy');
            // Keep the original AND-before-OR semantics, even when multiple labels coincide.
            point((!!ex2[i] && rising && ztSell === 2)
                || ((ztSell === 0 || ztSell === 2) && !!ex2[i]), '[1 卖]', 'sell');
        }
        const ma = map(i => i < 19 ? missing : close.slice(i - 19, i + 1).reduce((s, v) => s + v, 0) / 20);
        addLine('缠论MA20', '#ff00ff', false, ma, 2);
        addLine('缠论MA20下降', '#00ff00', false, map(i => ma[i] < ref(ma, i) ? ma[i] : missing), 2);
        // GET:=ZIG(3,5) is assignment, not an output; do not invent a yellow ZIG line.
        // Dead VAR/库存能量 calculations do not feed the actual labels and are intentionally omitted.
    }
    return output;
}

/** Optional Xueqiu display extension: reuse the Zhihu formula's points verbatim. */
export function buildChanBuySellPoints(
    dates: readonly string[], candles: readonly (readonly unknown[])[],
): ChanPoint[] {
    return buildChanOverlay(dates, candles, 'zhihu').points;
}
