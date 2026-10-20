'use client';

interface AmountHistoryItem {
  tradeDate: string;
  totalAmount: number;
  amountMa20: number;
  amountRatio20: number;
}

interface RiskNote {
  level: 'low' | 'medium' | 'high';
  title: string;
  description: string;
}

interface AmountAndRiskPanelProps {
  amountHistory?: AmountHistoryItem[];
  riskNotes?: RiskNote[];
}

export function AmountAndRiskPanel({ amountHistory, riskNotes }: AmountAndRiskPanelProps) {
  const recentData = amountHistory?.slice(-20) || [];
  const latest = recentData[recentData.length - 1];

  const formatAmount = (amount: number) => {
    if (amount >= 100000000) {
      return `${(amount / 100000000).toFixed(1)}亿`;
    } else if (amount >= 10000) {
      return `${(amount / 10000).toFixed(0)}万`;
    }
    return amount.toFixed(0);
  };

  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>06</span>
          <div>
            <h2>成交与风险</h2>
            <p>量能提示</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body flex flex-col gap-3">
        {/* 成交额数据 */}
        {recentData.length > 0 && (
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded bg-bg-secondary/50 p-2">
              <div className="text-xs text-text-secondary">今日成交</div>
              <div className="text-sm font-bold text-text-primary font-mono">
                {formatAmount(latest?.totalAmount || 0)}
              </div>
            </div>
            <div className="rounded bg-bg-secondary/50 p-2">
              <div className="text-xs text-text-secondary">20日均量</div>
              <div className="text-sm font-bold text-text-secondary font-mono">
                {formatAmount(latest?.amountMa20 || 0)}
              </div>
            </div>
            <div className="rounded bg-bg-secondary/50 p-2">
              <div className="text-xs text-text-secondary">量能比</div>
              <div className="text-sm font-bold text-text-primary font-mono">
                ×{(latest?.amountRatio20 || 1).toFixed(2)}
              </div>
            </div>
          </div>
        )}

        {/* 成交额图表 */}
        {recentData.length > 0 ? (
          <div className="h-[70px] w-full">
            <svg className="h-full w-full" viewBox="0 0 300 70">
              {recentData.map((item, i) => {
                const maxAmount = Math.max(...recentData.map(d => d.totalAmount), 1);
                const barWidth = 10;
                const gap = 1;
                const x = i * (barWidth + gap) + 10;
                const height = (item.totalAmount / maxAmount) * 55;
                return (
                  <rect key={i} x={x} y={63 - height} width={barWidth} height={height} fill="#6366f1" rx="1" />
                );
              })}
            </svg>
          </div>
        ) : (
          <div className="dashboard-empty h-[70px]">暂无成交数据</div>
        )}

        {/* 风险提示 */}
        {riskNotes && riskNotes.length > 0 ? (
          <div className="space-y-2 text-xs">
            {riskNotes.map((note, index) => (
              <div key={index} className="flex items-start gap-2 rounded bg-bg-secondary/50 p-2">
                <span
                  className={`flex-shrink-0 ${
                    note.level === 'high' ? 'text-danger' :
                    note.level === 'medium' ? 'text-warning' :
                    'text-success'
                  }`}
                >
                  ●
                </span>
                <div className="flex-1">
                  <div className="font-medium text-text-primary">{note.title}</div>
                  <div className="text-text-secondary">{note.description}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded bg-bg-secondary/50 p-3 text-center text-sm text-text-secondary">
            暂无风险提示
          </div>
        )}
      </div>
    </div>
  );
}
