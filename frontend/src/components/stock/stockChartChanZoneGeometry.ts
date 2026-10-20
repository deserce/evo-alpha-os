export interface ChanZoneEdge {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    dotted: boolean;
}

export interface ChanZoneBounds {
    x: number;
    y: number;
    width: number;
    height: number;
}

/** Clip real rectangle edges individually; never invent edges at viewport boundaries.
 * Dotted flags refer to the screen top/bottom after coordinate normalization.
 * Coincident edges merge with solid taking precedence; point-only edges are omitted.
 */
export function buildChanZoneEdges(
    start: readonly [number, number],
    end: readonly [number, number],
    bounds: ChanZoneBounds,
    dashedTop: boolean,
    dashedBottom: boolean,
): ChanZoneEdge[] {
    const values = [...start, ...end, bounds.x, bounds.y, bounds.width, bounds.height];
    if (!values.every(Number.isFinite) || bounds.width <= 0 || bounds.height <= 0) return [];
    const right = bounds.x + bounds.width;
    const bottom = bounds.y + bounds.height;
    if (!Number.isFinite(right) || !Number.isFinite(bottom)) return [];
    const leftX = Math.min(start[0], end[0]);
    const rightX = Math.max(start[0], end[0]);
    const topY = Math.min(start[1], end[1]);
    const bottomY = Math.max(start[1], end[1]);
    const edges: ChanZoneEdge[] = [];
    const append = (candidate: ChanZoneEdge): void => {
        const duplicate = edges.find(edge => edge.x1 === candidate.x1
            && edge.y1 === candidate.y1 && edge.x2 === candidate.x2
            && edge.y2 === candidate.y2);
        if (duplicate) duplicate.dotted = duplicate.dotted && candidate.dotted;
        else edges.push(candidate);
    };
    const horizontal = (y: number, dotted: boolean): void => {
        const x1 = Math.max(leftX, bounds.x);
        const x2 = Math.min(rightX, right);
        if (y >= bounds.y && y <= bottom && x1 < x2) {
            append({ x1, y1: y, x2, y2: y, dotted });
        }
    };
    const vertical = (x: number): void => {
        const y1 = Math.max(topY, bounds.y);
        const y2 = Math.min(bottomY, bottom);
        if (x >= bounds.x && x <= right && y1 < y2) {
            append({ x1: x, y1, x2: x, y2, dotted: false });
        }
    };
    horizontal(topY, dashedTop);
    horizontal(bottomY, dashedBottom);
    vertical(leftX);
    vertical(rightX);
    return edges;
}
