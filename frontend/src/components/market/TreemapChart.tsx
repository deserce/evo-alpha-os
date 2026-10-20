'use client';

import ReactECharts from 'echarts-for-react';
import { useQuery } from '@tanstack/react-query';
import { quantApi } from '@/lib/api';
import { useState } from 'react';
import { clsx } from 'clsx'; 

const RPS_OPTIONS = [
  { label: '5D', value: 'sector_rps_5' },
  { label: '10D', value: 'sector_rps_10' },
  { label: '20D', value: 'sector_rps_20' },
  { label: '50D', value: 'sector_rps_50' },
  { label: '120D', value: 'sector_rps_120' },
  { label: '250D', value: 'sector_rps_250' },
];

export default function TreemapChart() {
  const [period, setPeriod] = useState('sector_rps_20');

  const { data: sectorData, isLoading } = useQuery({
    queryKey: ['sectorHeatmap', period],
    queryFn: () => quantApi.getIndustryStrength(undefined, 100, period),
  });

  if (isLoading) return <div className="h-[400px] w-full bg-bg-secondary border border-border-primary animate-pulse"></div>;
  const rows = Array.isArray(sectorData?.rows) ? sectorData.rows : [];
  if (!rows.length) return null;

  let processedData = [...rows].sort((a: any, b: any) => (Number(b[period]) || 0) - (Number(a[period]) || 0));
  let displayData = processedData.length > 46 ? [...processedData.slice(0, 23), ...processedData.slice(-23)] : processedData;

  const chartData = displayData.map((item: any) => ({
    name: item.sector_name,
    value: [Number(item[period]) || 0, Number(item.return_20d) || 0],
  }));

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: '#1c2127',
      borderColor: '#30363d',
      padding: [8, 12],
      textStyle: { color: '#d1d1d1', fontSize: 12, fontFamily: 'monospace' },
      formatter: (params: any) => {
        const val = params.value;
        const color = val[1] >= 0 ? '#ff4d4d' : '#00c087';
        return `<div style="font-weight:bold;margin-bottom:4px">${params.name}</div>
                <div style="display:flex;justify-content:space-between;gap:12px">
                  <span style="color:#8a8d91 text-xs">RPS Strength:</span>
                  <span style="font-weight:bold">${val[0].toFixed(0)}</span>
                </div>
                <div style="display:flex;justify-content:space-between;gap:12px">
                  <span style="color:#8a8d91 text-xs">20D Return:</span>
                  <span style="font-weight:bold;color:${color}">${val[1].toFixed(2)}%</span>
                </div>`;
      }
    },
    series: [{
      type: 'treemap',
      width: '100%', height: '100%',
      roam: false, nodeClick: false, breadcrumb: { show: false },
      label: {
        show: true,
        formatter: (params: any) => `${params.name}\n${params.value[1].toFixed(1)}%`,
        fontSize: 11,
        fontFamily: 'monospace',
        fontWeight: 'bold',
        color: '#fff'
      },
      itemStyle: { borderColor: '#080a0d', borderWidth: 1, gapWidth: 1 },
      data: chartData,
    }],
    visualMap: {
      type: 'continuous', dimension: 1,
      min: -5, max: 5,
      inRange: { color: ['#00c087', '#111417', '#ff4d4d'] }, // 微牛红绿：绿色(跌) -> 黑色 -> 红色(涨)
      show: false
    }
  };

  return (
    <div className="w-full bg-bg-secondary border border-border-primary flex flex-col mb-6">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-primary">
        <div className="flex items-center gap-2">
           <div className="w-1 h-3 bg-[#ff4d4d]"></div>
           <h3 className="text-text-primary text-xs font-bold uppercase tracking-wider">Mainline Heatmap</h3>
        </div>
        <div className="flex bg-[#080a0d] p-0.5 rounded border border-border-primary">
          {RPS_OPTIONS.map((opt) => (
            <button key={opt.value} onClick={() => setPeriod(opt.value)}
              className={clsx("px-2.5 py-1 text-[10px] font-bold transition-all", 
                period === opt.value ? "bg-[#1861ff] text-text-primary rounded-[3px]" : "text-text-secondary hover:text-text-primary")}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      <div className="h-[400px] w-full p-1">
        <ReactECharts option={option} style={{ height: '100%', width: '100%' }} notMerge={true} />
      </div>
    </div>
  );
}
