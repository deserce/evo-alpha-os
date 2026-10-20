'use client';

interface PortfolioRiskAlertsPanelProps {
  data?: any[];
  tradeDate?: string;
}

export function PortfolioRiskAlertsPanel({ data, tradeDate }: PortfolioRiskAlertsPanelProps) {
  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>10</span>
          <div>
            <h2>持仓风险</h2>
            <p>风险提醒</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body">
        {data && data.length > 0 ? (
          <div className="dashboard-table-wrap">
            <table className="dashboard-table">
              <thead>
                <tr>
                  <th>股票</th>
                  <th>状态</th>
                  <th>风险</th>
                </tr>
              </thead>
              <tbody>
                {data.map((item: any, index: number) => (
                  <tr key={index}>
                    <td>{item.symbol}</td>
                    <td>{item.status}</td>
                    <td>{item.risk}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="dashboard-empty">暂无数据</div>
        )}
      </div>
    </div>
  );
}
