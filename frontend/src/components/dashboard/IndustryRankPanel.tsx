'use client';

interface IndustryRankPanelProps {
  data?: any[];
  tradeDate?: string;
}

export function IndustryRankPanel({ data, tradeDate }: IndustryRankPanelProps) {
  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>05</span>
          <div>
            <h2>强行业榜</h2>
            <p>行业强度排行</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body">
        {data && data.length > 0 ? (
          <div className="dashboard-table-wrap">
            <table className="dashboard-table">
              <thead>
                <tr>
                  <th>排名</th>
                  <th>行业名称</th>
                  <th>状态</th>
                  <th className="is-right">强度分</th>
                </tr>
              </thead>
              <tbody>
                {data.slice(0, 8).map((item: any, index: number) => (
                  <tr key={index}>
                    <td>{index + 1}</td>
                    <td>{item.sector_name || item.industry_name}</td>
                    <td>{item.industry_state}</td>
                    <td className="is-right">{item.industry_strength_score?.toFixed(0) || 0}</td>
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
