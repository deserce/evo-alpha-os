'use client';

interface TodaySignalsPanelProps {
  data?: any[];
  tradeDate?: string;
}

export function TodaySignalsPanel({ data, tradeDate }: TodaySignalsPanelProps) {
  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>06</span>
          <div>
            <h2>今日买点</h2>
            <p>触发信号</p>
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
                  <th>买点</th>
                  <th className="is-right">结构分</th>
                </tr>
              </thead>
              <tbody>
                {data.slice(0, 8).map((item: any, index: number) => (
                  <tr key={index}>
                    <td>{item.name || item.symbol}</td>
                    <td>{item.signal_type}</td>
                    <td className="is-right">{item.structure_score?.toFixed(0) || 0}</td>
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
