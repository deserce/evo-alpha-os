'use client';

import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { marketApi } from '@/lib/api';
import { useMarketStore } from '@/lib/market-store';
import { Layers, Search, ArrowRight, BrainCircuit, MessageSquare, BarChart3, Loader2, TrendingUp } from 'lucide-react';
import { clsx } from 'clsx';
import { format } from 'date-fns';
import ReactECharts from 'echarts-for-react';

export default function SectorPage() {
  const { selectedSector, scrollTop, setSelectedSector, setScrollTop } = useMarketStore();
  const listContainerRef = useRef<HTMLDivElement>(null);

  const [indicators, setIndicators] = useState({
    vol: true,
    macd: true,
    rpsShort: true,
    rpsLong: false,
  });

  const toggleIndicator = (key: keyof typeof indicators) => {
    setIndicators(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const { data: sectors } = useQuery({
    queryKey: ['sectorListRPS'],
    queryFn: () => marketApi.getSectorHeatmap('rps_20'),
    staleTime: 1000 * 60 * 5, 
  });

  const { data: stocks } = useQuery({
    queryKey: ['sectorStocks', selectedSector],
    queryFn: () => marketApi.getSectorStocks(selectedSector),
  });

  const { data: chartData } = useQuery({
    queryKey: ['sectorChartDetail', selectedSector],
    queryFn: () => marketApi.getSectorChart(selectedSector),
  });

  useEffect(() => {
    if (stocks && listContainerRef.current) {
      requestAnimationFrame(() => {
        if (listContainerRef.current) {
          listContainerRef.current.scrollTop = scrollTop;
        }
      });
    }
  }, [stocks, scrollTop]);

  // --- ECharts 配置配色 Webull 化 ---
  const getChartOption = () => {
    if (!chartData?.dates?.length) return null;

    const gridTop = 8; 
    const gridBottom = 5;
    const totalHeight = 100 - gridTop - gridBottom;
    const activeSubCharts = (indicators.vol ? 1 : 0) + (indicators.macd ? 1 : 0) + ((indicators.rpsShort || indicators.rpsLong) ? 1 : 0);
    const subChartHeight = 12;
    const subChartGap = 2;
    const mainChartHeight = totalHeight - (activeSubCharts * (subChartHeight + subChartGap));

    let currentTop = gridTop + mainChartHeight + subChartGap;
    
    // 背景与轴线配色
    const axisColor = '#1e2226';
    const textColor = '#8a8d91';

    const grids = [{ left: '10', right: '50', top: `${gridTop}%`, height: `${mainChartHeight}%` }];
    const xAxes: any[] = [{ 
      type: 'category', data: chartData.dates, 
      axisLine: { lineStyle: { color: axisColor } }, 
      axisLabel: { show: activeSubCharts === 0, color: textColor, fontSize: 10, fontFamily: 'monospace' },
      axisTick: { show: false }
    }];
    const yAxes: any[] = [{ 
      scale: true, position: 'right', 
      splitLine: { lineStyle: { color: axisColor, type: 'dashed' } }, 
      axisLabel: { color: textColor, fontSize: 10, fontFamily: 'monospace' },
      axisLine: { show: true, lineStyle: { color: axisColor } }
    }];
    
    const series: any[] = [
      {
        name: 'K线', type: 'candlestick', data: chartData.kline,
        itemStyle: { color: '#ff4d4d', color0: '#00c087', borderColor: '#ff4d4d', borderColor0: '#00c087' }
      },
      // 均线对齐微牛色系
      { name: 'MA5', type: 'line', data: chartData.ma.ma5, smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#ffffff' } },
      { name: 'MA20', type: 'line', data: chartData.ma.ma20, smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#ffb11a' } },
    ];

    let gIdx = 1;

    if (indicators.vol) {
      grids.push({ left: '10', right: '50', top: `${currentTop}%`, height: `${subChartHeight}%` });
      xAxes.push({ type: 'category', gridIndex: gIdx, data: chartData.dates, axisLine: { show: false }, axisLabel: { show: false }, axisTick: { show: false } });
      yAxes.push({ scale: true, position: 'right', gridIndex: gIdx, splitNumber: 2, axisLabel: { show: false }, axisLine: { show: true, lineStyle: { color: axisColor } }, splitLine: { show: false } });
      series.push({
        name: 'Vol', type: 'bar', xAxisIndex: gIdx, yAxisIndex: gIdx, data: chartData.volume,
        itemStyle: { color: (params: any) => chartData.kline[params.dataIndex][1] > chartData.kline[params.dataIndex][0] ? '#ff4d4d' : '#00c087' }
      });
      currentTop += subChartHeight + subChartGap;
      gIdx++;
    }

    if (indicators.macd) {
      grids.push({ left: '10', right: '50', top: `${currentTop}%`, height: `${subChartHeight}%` });
      xAxes.push({ type: 'category', gridIndex: gIdx, data: chartData.dates, axisLine: { show: false }, axisLabel: { show: false }, axisTick: { show: false } });
      yAxes.push({ scale: true, position: 'right', gridIndex: gIdx, splitNumber: 2, axisLabel: { show: false }, axisLine: { show: true, lineStyle: { color: axisColor } }, splitLine: { show: false } });
      series.push(
        { name: 'MACD', type: 'bar', xAxisIndex: gIdx, yAxisIndex: gIdx, data: chartData.macd.bar, itemStyle: { color: (params: any) => params.value > 0 ? '#ff4d4d' : '#00c087' } },
        { name: 'DIFF', type: 'line', xAxisIndex: gIdx, yAxisIndex: gIdx, data: chartData.macd.diff, showSymbol: false, lineStyle: { width: 1, color: '#fff' } },
        { name: 'DEA', type: 'line', xAxisIndex: gIdx, yAxisIndex: gIdx, data: chartData.macd.dea, showSymbol: false, lineStyle: { width: 1, color: '#ffb11a' } }
      );
      currentTop += subChartHeight + subChartGap;
      gIdx++;
    }

    if (indicators.rpsShort || indicators.rpsLong) {
      grids.push({ left: '10', right: '50', top: `${currentTop}%`, height: `${subChartHeight}%` });
      xAxes.push({ type: 'category', gridIndex: gIdx, data: chartData.dates, axisLine: { show: true, lineStyle: { color: axisColor } }, axisLabel: { show: true, color: textColor, fontSize: 10, fontFamily: 'monospace' }, axisTick: { show: false } });
      yAxes.push({ scale: true, position: 'right', gridIndex: gIdx, min: 0, max: 100, axisLabel: { show: true, color: '#ffb11a', fontSize: 9, fontFamily: 'monospace' }, axisLine: { show: true, lineStyle: { color: axisColor } }, splitLine: { show: false } });
      series.push({
         type: 'line', xAxisIndex: gIdx, yAxisIndex: gIdx, markLine: { data: [{ yAxis: 90 }], symbol: 'none', lineStyle: { color: '#ff4d4d', type: 'dashed', opacity: 0.5 }, label: { show: false } }
      });
      if (indicators.rpsShort) {
        series.push(
          { name: 'R5', type: 'line', xAxisIndex: gIdx, yAxisIndex: gIdx, data: chartData.rps?.rps_5, showSymbol: false, lineStyle: { width: 1, color: '#a78bfa' } },
          { name: 'R20', type: 'line', xAxisIndex: gIdx, yAxisIndex: gIdx, data: chartData.rps?.rps_20, showSymbol: false, lineStyle: { width: 1, color: '#ffb11a' } }
        );
      }
      if (indicators.rpsLong) {
        series.push(
          { name: 'R50', type: 'line', xAxisIndex: gIdx, yAxisIndex: gIdx, data: chartData.rps?.rps_50, showSymbol: false, lineStyle: { width: 1, color: '#1861ff' } }
        );
      }
    }

    return {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross', crossStyle: { color: textColor } }, backgroundColor: '#111417', borderColor: axisColor, textStyle: { color: '#d1d1d1', fontSize: 11, fontFamily: 'monospace' } },
      axisPointer: { link: { xAxisIndex: 'all' } },
      grid: grids, xAxis: xAxes, yAxis: yAxes, series: series
    };
  };

  return (
    <div className="h-full w-full overflow-hidden flex flex-col bg-[#080a0d] text-[#d1d1d1]">
      
      {/* 顶部标题 - 压低高度，对齐微牛 */}
      <div className="h-12 flex items-center justify-between px-4 border-b border-[#1e2226] bg-[#111417] shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-1 h-4 bg-[#1861ff]"></div>
          <h1 className="text-sm font-bold text-white flex items-center gap-2 uppercase tracking-tight">
            Sector Room <span className="text-[#8a8d91] font-mono">/ {selectedSector}</span>
          </h1>
        </div>
        <div className="text-xs font-bold text-[#8a8d91] font-mono uppercase tracking-widest">{format(new Date(), 'yyyy-MM-dd')}</div>
      </div>

      {/* Grid 主体 */}
      <div className="flex-1 flex overflow-hidden p-1 gap-1">
        
        {/* 左侧: 板块列表 */}
        <div className="w-56 bg-[#111417] border border-[#1e2226] flex flex-col shrink-0">
           <div className="h-9 px-3 border-b border-[#1e2226] flex items-center gap-2 shrink-0 bg-[#1c2127]/30">
             <TrendingUp size={12} className="text-[#ffb11a]"/> 
             <span className="text-[11px] font-bold text-[#8a8d91] uppercase">Top Sectors (RPS)</span>
           </div>
           
           <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
                {!sectors?.length ? <div className="p-4 text-[10px] text-[#8a8d91] font-mono">LOADING...</div> : (
                   <div className="flex flex-col">
                     {sectors.map((sector: any) => (
                       <div 
                         key={sector.name}
                         onClick={() => { setSelectedSector(sector.name); setScrollTop(0); }}
                         className={clsx(
                           "px-3 py-2.5 cursor-pointer border-b border-[#1e2226] transition-all flex justify-between items-center group",
                           selectedSector === sector.name ? "bg-[#1861ff]/10 relative" : "hover:bg-[#1c2127]"
                         )}
                       >
                         {selectedSector === sector.name && <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-[#1861ff]" />}
                         <div className="flex flex-col min-w-0 flex-1">
                             <span className={clsx("text-[12px] font-bold truncate", selectedSector === sector.name ? "text-white" : "text-[#d1d1d1]")}>
                               {sector.name}
                             </span>
                             <span className={clsx("text-[10px] font-mono font-bold", sector.chg > 0 ? "text-[#ff4d4d]" : "text-[#00c087]")}>
                               {sector.chg > 0 ? '+' : ''}{sector.chg}%
                             </span>
                         </div>
                         <div className={clsx(
                           "w-7 h-5 flex items-center justify-center rounded-sm text-[10px] font-bold font-mono shrink-0",
                           sector.value >= 90 ? "bg-[#ff4d4d] text-white" : "bg-[#1e2226] text-[#8a8d91]"
                         )}>
                           {sector.value}
                         </div>
                       </div>
                     ))}
                   </div>
                )}
           </div>
        </div>

        {/* 中间: 图表 + 功能 */}
        <div className="flex-1 flex flex-col gap-1 min-w-0">
           
           {/* 图表区域 */}
           <div className="flex-[2] bg-[#111417] border border-[#1e2226] flex flex-col min-h-0 relative">
              <div className="h-9 px-3 border-b border-[#1e2226] flex justify-between items-center bg-[#1c2127]/30 shrink-0">
                 <div className="flex items-center gap-2 text-[11px] font-bold text-[#8a8d91] uppercase">
                    <BarChart3 size={12} className="text-[#ffb11a]"/> Index & Metrics
                 </div>
                 <div className="flex gap-1">
                    {['vol', 'macd', 'rpsShort', 'rpsLong'].map(k => (
                        <button key={k} onClick={() => toggleIndicator(k as any)} 
                            className={clsx("px-2 py-0.5 text-[9px] font-black rounded-sm border transition-all", 
                                indicators[k as keyof typeof indicators] ? "bg-[#1861ff]/10 border-[#1861ff] text-[#1861ff]" : "border-[#1e2226] text-[#8a8d91] hover:border-[#8a8d91]")}>
                            {k.replace('rps', 'RPS').toUpperCase()}
                        </button>
                    ))}
                 </div>
              </div>
              <div className="flex-1 w-full relative">
                 {!chartData ? (
                    <div className="w-full h-full flex items-center justify-center text-[#8a8d91] text-[10px] font-mono">LOADING DATA...</div>
                 ) : (
                    <ReactECharts option={getChartOption()} style={{ height: '100%', width: '100%', position: 'absolute' }} notMerge={true} />
                 )}
              </div>
           </div>

           {/* 下部功能区 */}
           <div className="h-48 grid grid-cols-2 gap-1 shrink-0">
              <div className="bg-[#111417] border border-[#1e2226] p-3 flex flex-col relative overflow-hidden">
                 <div className="flex items-center gap-2 text-[10px] font-bold text-[#8a8d91] mb-2 uppercase tracking-tighter shrink-0">
                    <MessageSquare size={12} /> News Monitor
                 </div>
                 <div className="flex-1 flex items-center justify-center border border-dashed border-[#1e2226] rounded-sm">
                    <div className="text-xs font-mono font-bold text-[#30363d]">NO FEED</div>
                 </div>
              </div>

              <div className="bg-[#111417] border border-[#1e2226] p-3 flex flex-col relative overflow-hidden">
                 <div className="flex items-center gap-2 text-[#1861ff] text-[10px] font-bold mb-2 uppercase tracking-tighter shrink-0">
                    <BrainCircuit size={12} /> AI Logic
                 </div>
                 <div className="flex-1 text-[11px] text-[#8a8d91] overflow-y-auto leading-relaxed custom-scrollbar font-medium">
                    <p>Monitoring {selectedSector} sector in real-time...</p>
                 </div>
                 <button className="mt-2 w-full py-1.5 bg-[#1861ff]/10 border border-[#1861ff]/30 hover:bg-[#1861ff]/20 text-[#1861ff] text-[10px] font-black rounded-sm uppercase transition-all">
                    Generate Report
                 </button>
              </div>
           </div>
        </div>

        {/* 右侧: 成分股列表 */}
        <div className="w-80 bg-[#111417] border border-[#1e2226] flex flex-col shrink-0">
           <div className="h-9 bg-[#1c2127]/30 border-b border-[#1e2226] flex items-center px-3 justify-between shrink-0">
             <span className="text-[11px] font-bold text-[#8a8d91] flex items-center gap-2 uppercase">
               <Search size={12} className="text-[#1861ff]"/> Components
             </span>
             <div className="text-[9px] text-[#8a8d91] font-mono">RPS TOP</div>
           </div>

           <div 
             ref={listContainerRef}
             className="flex-1 overflow-y-auto custom-scrollbar p-0"
             onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
           >
              {!stocks ? (
                <div className="h-full flex items-center justify-center text-[#8a8d91] text-[10px] font-mono">LOADING...</div>
              ) : (
                <table className="w-full text-left border-collapse">
                  <thead className="bg-[#1c2127] text-[#8a8d91] sticky top-0 z-10">
                    <tr>
                      <th className="px-3 py-2 text-[10px] font-bold uppercase">Name</th>
                      <th className="px-3 py-2 text-[10px] font-bold text-right uppercase">Chg%</th>
                      <th className="px-3 py-2 text-[10px] font-bold text-right uppercase">RPS</th>
                      <th className="px-1"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#1e2226]">
                    {stocks.map((stock: any) => (
                      <tr key={stock.code} className="hover:bg-[#1c2127] transition-colors group">
                        <td className="px-3 py-2">
                          <div className="text-[12px] font-bold text-[#d1d1d1] flex items-center gap-1">
                            {stock.name}
                            {stock.is_dragon && <span className="text-[8px] bg-[#ff4d4d] text-white px-0.5 rounded-sm">龙</span>}
                          </div>
                          <div className="text-[10px] font-mono text-[#8a8d91]">{stock.code}</div>
                        </td>
                        <td className={clsx("px-3 py-2 text-right font-mono font-bold text-[11px]", stock.zf > 0 ? "text-[#ff4d4d]" : "text-[#00c087]")}>
                          {stock.zf > 0 ? '+' : ''}{stock.zf}%
                        </td>
                        <td className="px-3 py-2 text-right">
                          <span className={clsx("text-[11px] font-mono font-bold", stock.rps >= 90 ? "text-[#ffb11a]" : "text-[#8a8d91]")}>
                            {stock.rps || '-'}
                          </span>
                        </td>
                        <td className="px-1 text-right">
                          <Link 
                            href={`/stock/${stock.code}`}
                            scroll={false} 
                            className="inline-flex items-center p-1.5 text-[#8a8d91] hover:text-[#1861ff] transition-all"
                          >
                            <ArrowRight size={14} />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
           </div>
        </div>

      </div>
    </div>
  );
}