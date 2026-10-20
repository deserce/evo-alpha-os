'use client';

import { useEffect, useRef } from 'react';

interface RiskNote {
  level: 'low' | 'medium' | 'high';
  title: string;
  description: string;
}

interface MarketOverviewProps {
  state: string;
  score: number;
  suggestedPositionMin: number;
  suggestedPositionMax: number;
  tradingPermission?: 'forbidden' | 'observe' | 'limited' | 'normal' | 'active';
  upCount?: number;
  downCount?: number;
  limitUpCount?: number;
  limitDownCount?: number;
  newHigh20Count?: number;
  rps90Count?: number;
  amount?: number;
  amountRatio20?: number;
  summary?: string;
  riskNotes?: RiskNote[];
}

const stateMeta: Record<string, { label: string; color: string; permission: string }> = {
  M0: { label: '极弱', color: '#ef4444', permission: '暂停新开仓' },
  M1: { label: '偏弱', color: '#f97316', permission: '观察为主' },
  M2: { label: '中性', color: '#eab308', permission: '小仓参与' },
  M3: { label: '偏强', color: '#2dd4bf', permission: '允许正常参与' },
  M4: { label: '强势', color: '#14b8a6', permission: '允许积极参与' },
};

const permissionText = {
  forbidden: '暂停新开仓',
  observe: '观察为主',
  limited: '小仓参与',
  normal: '允许正常参与',
  active: '允许积极参与',
};

function formatAmount(amount: number) {
  if (amount >= 100000000) return `${(amount / 100000000).toFixed(0)}亿`;
  if (amount >= 10000) return `${(amount / 10000).toFixed(0)}万`;
  return amount.toFixed(0);
}

function riskDotColor(level: RiskNote['level']) {
  if (level === 'high') return 'bg-danger';
  if (level === 'medium') return 'bg-warning';
  return 'bg-success';
}

export function MarketOverviewCard({
  state,
  score,
  suggestedPositionMin,
  suggestedPositionMax,
  tradingPermission,
  upCount = 0,
  downCount = 0,
  limitUpCount = 0,
  limitDownCount = 0,
  newHigh20Count = 0,
  rps90Count = 0,
  amount = 0,
  amountRatio20 = 1,
  summary = '',
  riskNotes = [],
}: MarketOverviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const meta = stateMeta[state] || stateMeta.M2;
  const upRatio = upCount + downCount > 0 ? (upCount / (upCount + downCount)) * 100 : 0;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    const centerX = width / 2;
    const centerY = height * 0.58;
    const radius = Math.min(width, height) * 0.36;
    const startAngle = Math.PI * 0.75;
    const endAngle = Math.PI * 2.25;
    const scoreAngle = startAngle + (Math.max(0, Math.min(score, 100)) / 100) * (endAngle - startAngle);

    ctx.clearRect(0, 0, width, height);
    ctx.lineCap = 'round';
    ctx.lineWidth = 13;
    ctx.strokeStyle = '#1e293b';
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, startAngle, endAngle);
    ctx.stroke();

    const gradient = ctx.createLinearGradient(centerX - radius, centerY, centerX + radius, centerY);
    gradient.addColorStop(0, '#ef4444');
    gradient.addColorStop(0.28, '#f97316');
    gradient.addColorStop(0.52, '#eab308');
    gradient.addColorStop(0.76, '#2dd4bf');
    gradient.addColorStop(1, '#14b8a6');
    ctx.strokeStyle = gradient;
    ctx.lineWidth = 11;
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, startAngle, scoreAngle);
    ctx.stroke();

    ctx.fillStyle = meta.color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '800 34px system-ui';
    ctx.fillText(score.toFixed(0), centerX, centerY - 8);
    ctx.font = '700 12px system-ui';
    ctx.fillText('/ 100', centerX, centerY + 20);
  }, [meta.color, score]);

  return (
    <section className="dashboard-panel min-h-[220px]">
      <div className="dashboard-panel-body">
        <div className="market-hero-grid grid gap-5 lg:grid-cols-[220px_1.2fr_0.9fr_1.15fr]">
          <div className="market-hero-gauge flex items-center gap-4">
            <canvas
              ref={canvasRef}
              width={190}
              height={150}
              className="market-hero-gauge-canvas"
              style={{ width: 190, height: 150 }}
            />
          </div>

          <div className="market-hero-state flex flex-col justify-center gap-3">
            <div>
              <span className="dashboard-eyebrow">市场状态</span>
              <div className="mt-1 flex items-baseline gap-3">
                <strong className="text-4xl font-black" style={{ color: meta.color }}>{state}</strong>
                <span className="text-lg font-bold text-text-primary">{meta.label}</span>
              </div>
            </div>
            <div>
              <span className="dashboard-pill is-green">
                {tradingPermission ? permissionText[tradingPermission] : meta.permission}
              </span>
            </div>
            <p className="max-w-[460px] text-sm leading-6 text-text-secondary">
              {summary || `市场处于 ${state}，强度分 ${score.toFixed(1)}。`}
            </p>
          </div>

          <div className="market-hero-position flex flex-col justify-center gap-4">
            <div>
              <span className="dashboard-eyebrow">建议仓位</span>
              <strong className="mt-1 block text-3xl font-black text-text-primary font-mono">
                {suggestedPositionMin}-{suggestedPositionMax}%
              </strong>
            </div>
            <div className="grid grid-cols-2 gap-x-5 gap-y-2 text-sm">
              <span className="text-text-tertiary">上涨占比</span>
              <b className="text-right font-mono text-text-primary">{upRatio.toFixed(1)}%</b>
              <span className="text-text-tertiary">涨跌停</span>
              <b className="text-right font-mono text-text-primary">{limitUpCount}/{limitDownCount}</b>
              <span className="text-text-tertiary">强势扩散</span>
              <b className="text-right font-mono text-text-primary">{newHigh20Count}/{rps90Count}</b>
              <span className="text-text-tertiary">成交额</span>
              <b className="text-right font-mono text-text-primary">{formatAmount(amount)} ×{amountRatio20.toFixed(2)}</b>
            </div>
          </div>

          <div className="market-hero-risk flex flex-col justify-center gap-3">
            <div>
              <span className="dashboard-eyebrow">风险提示</span>
              <h2 className="mt-1 text-base font-black text-text-primary">交易权限校验</h2>
            </div>
            <div className="space-y-2">
              {riskNotes.slice(0, 4).map((note) => (
                <div key={`${note.title}-${note.description}`} className="grid grid-cols-[10px_1fr] gap-2 text-sm">
                  <span className={`mt-1.5 h-2 w-2 rounded-full ${riskDotColor(note.level)}`} />
                  <div>
                    <b className="block text-text-primary">{note.title}</b>
                    <span className="text-xs leading-5 text-text-tertiary">{note.description}</span>
                  </div>
                </div>
              ))}
              {!riskNotes.length && <div className="dashboard-empty">暂无风险提示</div>}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
