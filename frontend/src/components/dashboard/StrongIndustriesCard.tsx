'use client';

interface StrongIndustriesCardProps {
  data?: {
    i4_count?: number;
    i3_count?: number;
    top_industries?: Array<{ sector_name: string; industry_state: string }>;
  };
  tradeDate?: string;
}

export function StrongIndustriesCard({ data, tradeDate }: StrongIndustriesCardProps) {
  const i4Count = data?.i4_count || 0;
  const i3Count = data?.i3_count || 0;

  return (
    <a href="/a/sector" className="dashboard-status-card">
      <div className="dashboard-status-head">
        <span>强行业概览</span>
      </div>
      <div className="dashboard-status-main">
        <strong>I4核心 {i4Count} · I3强势 {i3Count}</strong>
        <p>点击查看行业详情 →</p>
      </div>
    </a>
  );
}
