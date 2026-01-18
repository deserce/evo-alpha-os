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

export default function SectorMatrix() {
  const [period, setPeriod] = useState('rps_20');

  const { data, isLoading } = useQuery({
    queryKey: ['rpsMatrix', period],
    queryFn: () => marketApi.getRpsMatrix(period),
  });

  if (isLoading) return <div className="h-[1500px] w-full bg-[#111417] flex items-center justify-center text-[#8a8d91] animate-pulse border border-[#1e2226] rounded">数据加载中...</div>;
  if (!data || !data.data || data.data.length === 0) return <div className="h-[1500px] w-full bg-[#111417] flex items-center justify-center text-[#8a8d91] border border-[#1e2226] rounded text-xs font-mono">NO DATA AVAILABLE</div>;

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      position: 'top',
      backgroundColor: '#1c2127',
      borderColor: '#30363d',
      padding: [8, 12],
      textStyle: { color: '#d1d1d1', fontSize: 12, fontFamily: 'monospace' },
      formatter: (p: any) => {
        const date = data.dates[p.value[0]];
        const sector = data.sectors[p.value[1]];
        const score = p.value[2];
        return `<div style="color:#8a8d91;margin-bottom:4px">${date}</div><div style="font-weight:bold">${sector}: <span style="color:#1861ff">${score}</span></div>`;
      }
    },
    grid: { top: '40px', bottom: '20px', left: '110px', right: '10px' },
    xAxis: {
      type: 'category',
      data: data.dates,
      position: 'top',
      axisLabel: { color: '#8a8d91', fontSize: 10, fontFamily: 'monospace', fontWeight: 500 },
      axisLine: { show: true, lineStyle: { color: '#1e2226' } },
      axisTick: { show: false }
    },
    yAxis: {
      type: 'category',
      data: data.sectors,
      axisLabel: { color: '#d1d1d1', fontSize: 11, interval: 0, fontWeight: 500 },
      axisLine: { show: true, lineStyle: { color: '#1e2226' } },
      axisTick: { show: false }
    },
    visualMap: {
      min: 50, max: 100, calculable: false, show: false,
      inRange: {
        // Webull 风格的高级饱和度分级：中性灰 -> 深蓝 -> 科技蓝 -> 警示橙 -> 强势红
        color: ['#1e2226', '#162b45', '#1861ff', '#e67e22', '#ff4d4d', '#9b1c1c']
      }
    },
    series: [{
      type: 'heatmap',
      data: data.data,
      label: {
        show: true,
        fontSize: 10,
        color: '#fff',
        fontFamily: 'monospace', // 数字全部等宽
        formatter: (p: any) => p.value[2]
      },
      itemStyle: {
        borderColor: '#080a0d', // 单元格间距改为背景色，产生网格感
        borderWidth: 1,
      }
    }]
  };

  return (
    <div className="w-full bg-[#111417] border border-[#1e2226] flex flex-col overflow-hidden">
      {/* 头部：模仿 Webull 侧边栏标题栏 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2226] bg-[#1c2127]/30">
        <div className="flex items-center gap-2">
          <div className="w-1 h-3 bg-[#1861ff]"></div>
          <h3 className="text-[#d1d1d1] text-xs font-bold uppercase tracking-wider">Sector RPS Matrix</h3>
          <span className="text-[10px] text-[#8a8d91] ml-2 font-mono">Top 50 Sectors</span>
        </div>

        {/* 仿微牛 Segmented Control 按钮组 */}
        <div className="flex bg-[#080a0d] p-0.5 rounded border border-[#1e2226]">
          {RPS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setPeriod(opt.value)}
              className={clsx(
                "px-3 py-1 text-[10px] font-bold transition-all",
                period === opt.value
                  ? "bg-[#1861ff] text-white rounded-[3px]"
                  : "text-[#8a8d91] hover:text-white"
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="h-[1500px] w-full px-2">
        <ReactECharts option={option} style={{ height: '100%', width: '100%' }} notMerge={true} />
      </div>
    </div>
  );
}