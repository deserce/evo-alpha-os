'use client';

interface StrengthDiffusionItem {
  tradeDate: string;
  newHigh20Count: number;
  newHigh60Count: number;
  rps90Count: number;
}

interface StrengthDiffusionChartProps {
  strengthDiffusionHistory?: StrengthDiffusionItem[];
}

export function StrengthDiffusionChart({ strengthDiffusionHistory }: StrengthDiffusionChartProps) {
  const recentData = strengthDiffusionHistory?.slice(-30) || [];
  const latest = recentData[recentData.length - 1];

  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>05</span>
          <div>
            <h2>强势扩散</h2>
            <p>新高与RPS90</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body flex flex-col gap-3">
        {recentData.length > 0 ? (
          <>
            {/* 当前数值 */}
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">新高20</div>
                <div className="text-base font-bold text-warning">{latest?.newHigh20Count || 0}</div>
              </div>
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">新高60</div>
                <div className="text-base font-bold text-warning">{latest?.newHigh60Count || 0}</div>
              </div>
              <div className="rounded bg-bg-secondary/50 p-2">
                <div className="text-xs text-text-secondary">RPS90</div>
                <div className="text-base font-bold text-blue-500">{latest?.rps90Count || 0}</div>
              </div>
            </div>

            {/* 图表 */}
            <div className="flex-1 min-h-[120px] w-full relative">
              <svg className="h-full w-full" viewBox="0 0 300 120">
                <polyline
                  fill="none"
                  stroke="#3b82f6"
                  strokeWidth="2"
                  points={recentData.map((item, i) => {
                    const maxCount = Math.max(...recentData.map(d => d.rps90Count), 1);
                    const x = (i / (recentData.length - 1)) * 300;
                    const y = 110 - (item.rps90Count / maxCount) * 90;
                    return `${x},${y}`;
                  }).join(' ')}
                />
                <polyline
                  fill="none"
                  stroke="#f59e0b"
                  strokeWidth="1.5"
                  strokeDasharray="3 3"
                  points={recentData.map((item, i) => {
                    const maxCount = Math.max(...recentData.map(d => Math.max(d.newHigh20Count, d.rps90Count)), 1);
                    const x = (i / (recentData.length - 1)) * 300;
                    const y = 110 - (item.newHigh20Count / maxCount) * 90;
                    return `${x},${y}`;
                  }).join(' ')}
                />
              </svg>
              <div className="absolute top-0 right-0 flex gap-3 text-xs">
                <span className="flex items-center gap-1">
                  <span className="w-3 h-1 bg-blue-500 rounded"></span>RPS90
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-warning rounded border-dashed"></span>新高20
                </span>
              </div>
            </div>
          </>
        ) : (
          <div className="dashboard-empty">暂无历史数据</div>
        )}
      </div>
    </div>
  );
}
