'use client';

import ReactECharts from 'echarts-for-react';
import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import { useState } from 'react';
import { clsx } from 'clsx'; 

const RPS_OPTIONS = [
  { label: '5D', value: 'rps_5' },
  { label: '10D', value: 'rps_10' },
  { label: '20D', value: 'rps_20' },
  { label: '50D', value: 'rps_50' },
  { label: '120D', value: 'rps_120' },
  { label: '250D', value: 'rps_250' },
];

export default function TreemapChart() {
  const [period, setPeriod] = useState('rps_20');

  const { data: sectorData, isLoading } = useQuery({
    queryKey: ['sectorHeatmap', period],
    queryFn: () => marketApi.getSectorHeatmap(period),
  });

  if (isLoading) return <div className="h-[400px] w-full bg-[#111417] border border-[#1e2226] animate-pulse"></div>;
  if (!sectorData || sectorData.length === 0) return null;

  let processedData = [...sectorData].sort((a: any, b: any) => (b.chg || 0) - (a.chg || 0));
  let displayData = processedData.length > 46 ? [...processedData.slice(0, 23), ...processedData.slice(-23)] : processedData;

  const chartData = displayData.map((item: any) => ({
    name: item.name,
    value: [item.value || 0, item.chg || 0],
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
                  <span style="color:#8a8d91 text-xs">Today's Chg:</span>
                  <span style="font-weight:bold;color:${color}">${(val[1] * 100).toFixed(2)}%</span>
                </div>`;
      }
    },
    series: [{
      type: 'treemap',
      width: '100%', height: '100%',
      roam: false, nodeClick: false, breadcrumb: { show: false },
      label: {
        show: true,
        formatter: (params: any) => `${params.name}\n${(params.value[1] * 100).toFixed(1)}%`,
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
      min: -0.05, max: 0.05,
      inRange: { color: ['#00c087', '#111417', '#ff4d4d'] }, // 微牛红绿：绿色(跌) -> 黑色 -> 红色(涨)
      show: false
    }
  };

  return (
    <div className="w-full bg-[#111417] border border-[#1e2226] flex flex-col mb-6">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2226]">
        <div className="flex items-center gap-2">
           <div className="w-1 h-3 bg-[#ff4d4d]"></div>
           <h3 className="text-[#d1d1d1] text-xs font-bold uppercase tracking-wider">Mainline Heatmap</h3>
        </div>
        <div className="flex bg-[#080a0d] p-0.5 rounded border border-[#1e2226]">
          {RPS_OPTIONS.map((opt) => (
            <button key={opt.value} onClick={() => setPeriod(opt.value)}
              className={clsx("px-2.5 py-1 text-[10px] font-bold transition-all", 
                period === opt.value ? "bg-[#1861ff] text-white rounded-[3px]" : "text-[#8a8d91] hover:text-white")}
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