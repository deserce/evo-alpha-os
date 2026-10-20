'use client';

interface PortfolioRiskCardProps {
  data?: any;
  tradeDate?: string;
}

export function PortfolioRiskCard({ data, tradeDate }: PortfolioRiskCardProps) {
  return (
    <a href="/a/portfolio" className="dashboard-status-card">
      <div className="dashboard-status-head">
        <span>持仓风险</span>
      </div>
      <div className="dashboard-status-main">
        <strong>-</strong>
        <p>{data ? '点击查看详情' : '暂无持仓数据'}</p>
      </div>
    </a>
  );
}
