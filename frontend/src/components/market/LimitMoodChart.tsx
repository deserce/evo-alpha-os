'use client';

interface LimitStatsItem {
  tradeDate: string;
  limitUpCount: number;
  limitDownCount: number;
}

interface LimitMoodChartProps {
  limitStatsHistory?: LimitStatsItem[];
}

export function LimitMoodChart({ limitStatsHistory }: LimitMoodChartProps) {
  const recentData = limitStatsHistory?.slice(-20) || [];
  const latest = recentData[recentData.length - 1];

  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>04</span>
          <div>
            <h2>涨跌停</h2>
            <p>情绪统计</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body flex flex-col gap-3">
        {recentData.length > 0 ? (
          <>
            {/* 当前数值 */}
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">涨停</div>
                <div className="text-base font-bold text-up">{latest?.limitUpCount || 0}</div>
              </div>
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">跌停</div>
                <div className="text-base font-bold text-down">{latest?.limitDownCount || 0}</div>
              </div>
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">情绪比</div>
                <div className="text-base font-bold text-text-primary font-mono">
                  {latest?.limitDownCount && latest.limitDownCount > 0
                    ? (latest.limitUpCount / latest.limitDownCount).toFixed(1)
                    : '-'}
                </div>
              </div>
            </div>

            {/* 图表 */}
            <div className="flex-1 min-h-[130px] w-full">
              <svg className="h-full w-full" viewBox="0 0 300 130">
                {recentData.map((item, i) => {
                  const maxCount = Math.max(...recentData.map(d => Math.max(d.limitUpCount, d.limitDownCount)), 1);
                  const barWidth = 9;
                  const gap = 1;
                  const x = i * (barWidth * 2 + gap) + 8;
                  const upHeight = (item.limitUpCount / maxCount) * 100;
                  const downHeight = (item.limitDownCount / maxCount) * 100;
                  return (
                    <g key={i}>
                      <rect x={x} y={115 - upHeight} width={barWidth} height={upHeight} fill="#ff4f6d" rx="1" />
                      <rect x={x + barWidth} y={115 - downHeight} width={barWidth} height={downHeight} fill="#28e070" rx="1" />
                    </g>
                  );
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
