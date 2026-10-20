'use client';

interface AiDailyBriefPanelProps {
  data?: any;
  tradeDate?: string;
}

export function AiDailyBriefPanel({ data, tradeDate }: AiDailyBriefPanelProps) {
  return (
    <div className="dashboard-panel">
      <div className="dashboard-panel-head">
        <div className="dashboard-panel-title">
          <span>07</span>
          <div>
            <h2>AI Brief</h2>
            <p>每日作战简报</p>
          </div>
        </div>
      </div>
      <div className="dashboard-panel-body">
        <div className="dashboard-brief-box">
          <div className="text-[11px] text-text-secondary">
            {data?.summary || '暂无 AI 分析内容'}
          </div>
        </div>
      </div>
    </div>
  );
}
