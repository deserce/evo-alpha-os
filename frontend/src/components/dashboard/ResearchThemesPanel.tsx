'use client';

interface ResearchThemesPanelProps {
  data?: any;
  tradeDate?: string;
}

export function ResearchThemesPanel({ data, tradeDate }: ResearchThemesPanelProps) {
  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>08</span>
          <div>
            <h2>研究池动态</h2>
            <p>研究主题追踪</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body">
        {data ? (
          <div className="dashboard-empty">暂无主题数据</div>
        ) : (
          <div className="dashboard-empty">暂无数据</div>
        )}
      </div>
    </div>
  );
}
