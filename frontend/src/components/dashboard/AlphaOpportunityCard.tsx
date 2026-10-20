'use client';

interface AlphaOpportunityCardProps {
  data?: {
    s4_count?: number;
    s5_count?: number;
    opportunities?: Array<any>;
  };
  tradeDate?: string;
}

export function AlphaOpportunityCard({ data, tradeDate }: AlphaOpportunityCardProps) {
  const s4Count = data?.s4_count || data?.opportunities?.filter((o: any) => o.lifecycle_state === 'S4').length || 0;
  const s5Count = data?.s5_count || data?.opportunities?.filter((o: any) => o.lifecycle_state === 'S5').length || 0;

  return (
    <a href="/a/alpha-pool" className="dashboard-status-card">
      <div className="dashboard-status-head">
        <span>Alpha机会</span>
      </div>
      <div className="dashboard-status-main">
        <strong>S4准备 {s4Count} · S5触发 {s5Count}</strong>
        <p>共 {s4Count + s5Count} 只机会</p>
      </div>
    </a>
  );
}
