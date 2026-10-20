'use client';

interface CoreAlphaPoolPanelProps {
  data?: any[];
  tradeDate?: string;
}

export function CoreAlphaPoolPanel({ data, tradeDate }: CoreAlphaPoolPanelProps) {
  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>09</span>
          <div>
            <h2>核心共振池</h2>
            <p>技术强度×产业研究</p>
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
                  <th>生命周期</th>
                  <th className="is-right">RPS20</th>
                </tr>
              </thead>
              <tbody>
                {data.slice(0, 8).map((item: any, index: number) => (
                  <tr key={index}>
                    <td>{item.name || item.symbol}</td>
                    <td>{item.lifecycle_state}</td>
                    <td className="is-right">{item.rps_20?.toFixed(1) || 0}</td>
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
