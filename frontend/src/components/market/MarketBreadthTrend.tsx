'use client';

interface BreadthHistoryItem {
  tradeDate: string;
  upCount: number;
  downCount: number;
  upRatio: number;
}

interface MarketBreadthTrendProps {
  breadthHistory?: BreadthHistoryItem[];
}

export function MarketBreadthTrend({ breadthHistory }: MarketBreadthTrendProps) {
  const recentData = breadthHistory?.slice(-30) || [];
  const latest = recentData[recentData.length - 1];

  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>03</span>
          <div>
            <h2>宽度趋势</h2>
            <p>上涨占比</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body flex flex-col gap-3">
        {recentData.length > 0 ? (
          <>
            {/* 当前数值 */}
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">上涨</div>
                <div className="text-base font-bold text-up">{latest?.upCount || 0}</div>
              </div>
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">下跌</div>
                <div className="text-base font-bold text-down">{latest?.downCount || 0}</div>
              </div>
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">占比</div>
                <div className="text-base font-bold text-text-primary font-mono">
                  {latest?.upRatio.toFixed(1) || 0}%
                </div>
              </div>
            </div>

            {/* 图表 */}
            <div className="flex-1 min-h-[140px] w-full">
              <svg className="h-full w-full" viewBox="0 0 300 140">
                <line x1="0" y1="70" x2="300" y2="70" stroke="#475569" strokeWidth="1" strokeDasharray="4 4" />
                <text x="5" y="65" fontSize="10" fill="#64748b">50%</text>

                <polyline
                  fill="none"
                  stroke="#ff4f6d"
                  strokeWidth="2"
                  points={recentData.map((item, i) => {
                    const x = (i / (recentData.length - 1)) * 300;
                    const y = 140 - (item.upRatio / 100) * 140;
                    return `${x},${y}`;
                  }).join(' ')}
                />

                {recentData.map((item, i) => {
                  const x = (i / (recentData.length - 1)) * 300;
                  const y = 140 - (item.upRatio / 100) * 140;
                  return <circle key={i} cx={x} cy={y} r="2" fill="#ff4f6d" />;
                })}
              </svg>
            </div>
          </>
        ) : (
          <div className="dashboard-empty">暂无历史数据</div>
        )}
      </div>
    </div>
  );
}
