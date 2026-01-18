'use client';

import ReactECharts from 'echarts-for-react';
import { useQuery } from '@tanstack/react-query';
import { stockApi } from '@/lib/api';
import { useState } from 'react';
import { clsx } from 'clsx';

interface StockChartProps {
  symbol: string;
}

function calculateMA(dayCount: number, data: any[]) {
  const result = [];
  for (let i = 0, len = data.length; i < len; i++) {
    if (i < dayCount) {
      result.push('-');
      continue;
    }
    let sum = 0;
    for (let j = 0; j < dayCount; j++) {
      sum += +data[i - j][1];
    }
    result.push((sum / dayCount).toFixed(2));
  }
  return result;
}

export default function StockChart({ symbol }: StockChartProps) {
  const [period, setPeriod] = useState('daily'); 
  
  const [indicators, setIndicators] = useState({
    vol: true, macd: true, rps_short: true, rps_long: false, kdj: false, north: false, fund: false
  });

  const { data, isLoading } = useQuery({
    queryKey: ['stockChart', symbol, period], 
    queryFn: () => stockApi.getChartData(symbol, period),
    staleTime: 5000,
  });

  if (isLoading) return <div className="h-full w-full flex items-center justify-center text-[#8a8d91] font-mono text-xs">LOADING...</div>;
  if (!data || !data.kline || data.kline.length === 0) return <div className="h-full w-full flex items-center justify-center text-[#8a8d91] font-mono text-xs">NO DATA</div>;

  const dates = data.dates;
  const klineData = data.kline;
  const volumes = data.volume;
  
  const ma5 = calculateMA(5, klineData);
  const ma20 = calculateMA(20, klineData);
  const ma50 = calculateMA(50, klineData);

  const activeIndicatorsCount = Object.values(indicators).filter(Boolean).length;
  const subChartHeight = 10; 
  const mainChartHeight = 100 - 18 - (activeIndicatorsCount * (subChartHeight + 2)); 

  let currentTop = 6; 
  const grid = [
    { left: '10', right: '50', top: `${currentTop}%`, height: `${mainChartHeight}%` }
  ];
  currentTop += mainChartHeight + 2;

  const xAxis: any[] = [{ type: 'category', data: dates, gridIndex: 0, axisLine: { show: false }, axisLabel: { show: false }, axisTick: { show: false } }];
  const yAxis: any[] = [{ 
    scale: true, position: 'right', gridIndex: 0, 
    splitLine: { lineStyle: { color: '#1e2226', type: 'dashed' } }, 
    axisLabel: { color: '#8a8d91', fontSize: 10, fontFamily: 'monospace' },
    axisLine: { show: true, lineStyle: { color: '#1e2226' } }
  }];
  
  const series: any[] = [
    {
      name: 'K线', type: 'candlestick', data: klineData,
      itemStyle: { color: '#ff4d4d', color0: '#00c087', borderColor: '#ff4d4d', borderColor0: '#00c087' },
      xAxisIndex: 0, yAxisIndex: 0
    },
    // Webull 均线配色：白、黄、蓝
    { name: 'MA5', type: 'line', data: ma5, smooth: true, lineStyle: { opacity: 0.9, width: 1, color: '#ffffff' }, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none' },
    { name: 'MA20', type: 'line', data: ma20, smooth: true, lineStyle: { opacity: 0.9, width: 1, color: '#ffb11a' }, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none' },
    { name: 'MA50', type: 'line', data: ma50, smooth: true, lineStyle: { opacity: 0.9, width: 1, color: '#1861ff' }, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none' }
  ];

  let gridIndex = 1;

  const addSubChart = (name: string, yAxisConfig: any, seriesList: any[]) => {
    grid.push({ left: '10', right: '50', top: `${currentTop}%`, height: `${subChartHeight}%` });
    xAxis.push({ type: 'category', data: dates, gridIndex: gridIndex, axisLine: { show: false }, axisLabel: { show: false }, axisTick: { show: false } });
    yAxis.push({ ...yAxisConfig, position: 'right', gridIndex: gridIndex, axisLine: { show: true, lineStyle: { color: '#1e2226' } } });
    seriesList.forEach(s => {
        series.push({ ...s, xAxisIndex: gridIndex, yAxisIndex: gridIndex });
    });
    currentTop += subChartHeight + 2;
    gridIndex++;
  };

  if (indicators.vol) {
    addSubChart('Vol', { scale: true, splitLine: { show: false }, axisLabel: { show: false } }, [
        { name: '成交量', type: 'bar', data: volumes, itemStyle: { color: (p:any) => { const i=p.dataIndex; return klineData[i] && klineData[i][1]>klineData[i][0]?'#ff4d4d':'#00c087'} } }
    ]);
  }
  if (indicators.macd) {
      addSubChart('MACD', { scale: true, splitLine: { show: false }, axisLabel: { show: false } }, [
        { name: 'MACD', type: 'bar', data: data.macd.hist, itemStyle: { color: (p: any) => p.value > 0 ? '#ff4d4d' : '#00c087' } },
        { name: 'DIF', type: 'line', data: data.macd.diff, lineStyle: { width: 1, color: '#fff' }, symbol: 'none' },
        { name: 'DEA', type: 'line', data: data.macd.dea, lineStyle: { width: 1, color: '#ffb11a' }, symbol: 'none' }
      ]);
  }
  
  if (indicators.rps_short) {
      addSubChart('RPS短', { min: 0, max: 100, splitLine: { show: false }, axisLabel: { color: '#ffb11a', fontSize: 10, fontFamily: 'monospace' } }, [
        { name: 'RPS 5', type: 'line', data: data.rps_short.r5, lineStyle: { width: 1, color: '#a78bfa' }, symbol: 'none' },
        { name: 'RPS 10', type: 'line', data: data.rps_short.r10, lineStyle: { width: 1, color: '#34d399' }, symbol: 'none' },
        { name: 'RPS 20', type: 'line', data: data.rps_short.r20, lineStyle: { width: 2, color: '#ffb11a' }, symbol: 'none' },
        { type: 'line', markLine: { symbol: 'none', silent: true, data: [{ yAxis: 90 }], lineStyle: { type: 'dashed', color: '#ff4d4d', width: 1 }, label: { show: true, position: 'end', formatter: '90', color: '#ff4d4d', fontSize: 10 } } }
      ]);
  }

  if (indicators.rps_long) {
      addSubChart('RPS长', { min: 0, max: 100, splitLine: { show: false }, axisLabel: { color: '#1861ff', fontSize: 10, fontFamily: 'monospace' } }, [
        { name: 'RPS 50', type: 'line', data: data.rps_long.r50, lineStyle: { width: 1, color: '#1861ff' }, symbol: 'none' },
        { name: 'RPS 120', type: 'line', data: data.rps_long.r120, lineStyle: { width: 1, color: '#818cf8' }, symbol: 'none' },
        { name: 'RPS 250', type: 'line', data: data.rps_long.r250, lineStyle: { width: 1, type: 'dashed', color: '#f472b6' }, symbol: 'none' },
        { type: 'line', markLine: { symbol: 'none', silent: true, data: [{ yAxis: 90 }], lineStyle: { type: 'dashed', color: '#ff4d4d', width: 1 }, label: { show: true, position: 'end', formatter: '90', color: '#ff4d4d', fontSize: 10 } } }
      ]);
  }

  if (indicators.north) {
       addSubChart('北向', { scale: true, splitLine: { show: false }, axisLabel: { show: false } }, [
         { name: '北向资金', type: 'line', data: data.funds.north, itemStyle: { color: '#d8b4fe' }, symbol: 'none', areaStyle: { opacity: 0.05 } }
       ]);
  }

  if (indicators.fund) {
       addSubChart('基金', { scale: true, splitLine: { show: false }, axisLabel: { show: false } }, [
         { name: '基金持仓', type: 'line', data: data.funds.fund, itemStyle: { color: '#1861ff' }, symbol: 'none', step: 'end' }
       ]);
  }

  if (xAxis.length > 0) {
      xAxis[xAxis.length - 1].axisLabel = { show: true, color: '#8a8d91', fontSize: 10, fontFamily: 'monospace' };
      xAxis[xAxis.length - 1].axisLine = { show: true, lineStyle: { color: '#1e2226' } };
  }

  const option = {
    backgroundColor: '#080a0d',
    animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', crossStyle: { color: '#8a8d91' } }, 
      backgroundColor: '#111417', borderColor: '#1e2226', textStyle: { color: '#d1d1d1', fontSize: 11, fontFamily: 'monospace' },
      position: function (pos: any, params: any, el: any, elRect: any, size: any) { const obj: any = { top: 10 }; obj[['left', 'right'][+(pos[0] < size.viewSize[0] / 2)]] = 30; return obj; }
    },
    axisPointer: { link: { xAxisIndex: 'all' } },
    dataZoom: [
        { type: 'inside', xAxisIndex: xAxis.map((_, i) => i), start: 60, end: 100 }, 
        { type: 'slider', xAxisIndex: xAxis.map((_, i) => i), bottom: 5, height: 14, borderColor: 'transparent', fillerColor: 'rgba(24, 97, 255, 0.1)', handleStyle: { color: '#1861ff' }, textStyle: { show: false } }
    ],
    grid: grid, xAxis: xAxis, yAxis: yAxis, series: series
  };

  const toggleIndicator = (key: keyof typeof indicators) => {
    setIndicators(prev => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="w-full h-full flex flex-col bg-[#111417] overflow-hidden">
        {/* 工具栏 - Webull 极简窄版 */}
        <div className="flex items-center justify-between px-2 py-1.5 border-b border-[#1e2226] bg-[#080a0d] shrink-0">
            <div className="flex gap-0.5">
                {['daily', 'weekly', 'monthly'].map(p => (
                    <button key={p} onClick={() => setPeriod(p)} className={clsx("px-2.5 py-1 text-[10px] font-bold transition-all", period === p ? "text-[#1861ff] border-b border-[#1861ff]" : "text-[#8a8d91] hover:text-white")}>
                        {p === 'daily' ? '日K' : p === 'weekly' ? '周K' : '月K'}
                    </button>
                ))}
            </div>
            <div className="flex gap-1">
                {[
                    { key: 'vol', label: 'VOL' }, { key: 'macd', label: 'MACD' },
                    { key: 'rps_short', label: 'RPS短' }, { key: 'rps_long', label: 'RPS长' },
                    { key: 'north', label: '北向' }, { key: 'fund', label: '基金' },
                ].map(ind => (
                    <button key={ind.key} onClick={() => toggleIndicator(ind.key as any)} className={clsx("px-2 py-0.5 text-[9px] font-black rounded-[2px] border transition-all", indicators[ind.key as keyof typeof indicators] ? "bg-[#1861ff]/10 border-[#1861ff] text-[#1861ff]" : "border-[#1e2226] text-[#8a8d91] hover:border-[#8a8d91]")}>
                        {ind.label}
                    </button>
                ))}
            </div>
        </div>
      <div className="flex-1 w-full relative">
         <ReactECharts option={option} style={{ height: '100%', width: '100%', position: 'absolute' }} notMerge={true} />
      </div>
    </div>
  );
}