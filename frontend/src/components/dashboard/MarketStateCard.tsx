'use client';

interface MarketStateCardProps {
  data?: {
    trade_date?: string;
    market_state?: string;
    market_strength_score?: number;
    up_count?: number;
    down_count?: number;
  };
  tradeDate?: string;
}

export function MarketStateCard({ data, tradeDate }: MarketStateCardProps) {
  if (!data) {
    return (
      <a href="/a/market" className="dashboard-status-card">
        <div className="dashboard-status-head">
          <span>市场状态</span>
        </div>
        <div className="dashboard-status-main">
          <strong>暂无数据</strong>
          <p>等待市场数据同步</p>
        </div>
      </a>
    );
  }

  const { market_state = 'M?', market_strength_score = 0, up_count = 0, down_count = 0 } = data;

  return (
    <a href="/a/market" className="dashboard-status-card">
      <div className="dashboard-status-head">
        <span>市场状态</span>
      </div>
      <div className="dashboard-status-main">
        <strong>{market_state}</strong>
        <p>强度分 {market_strength_score?.toFixed(0) || 0} · 涨{up_count} 跌{down_count}</p>
      </div>
      <div className="dashboard-status-foot">
        <span>点击查看详情 →</span>
      </div>
    </a>
  );
}
