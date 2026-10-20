'use client';

import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import ReactECharts from 'echarts-for-react';
import { LineChart } from 'lucide-react';
import { useGlobalStore } from '@/lib/store';

// 使用国证A指作为全A指数的代表
const ALLA_INDEX_CODE = '399317'; // 国证A指

export default function AllIndexChart({ days = 60 }: { days?: number }) {
  const { globalDate } = useGlobalStore();

  const { data, isLoading } = useQuery({
    queryKey: ['allIndexChart', globalDate, days],
    queryFn: () => marketApi.getIndexKline(ALLA_INDEX_CODE, days),
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <div className="w-full h-[200px] bg-bg-secondary border border-border-primary rounded-lg flex items-center justify-center">
        <div className="text-text-secondary text-xs animate-pulse font-mono">Loading Chart Data...</div>
      </div>
    );
  }

  if (!data || !data?.kline || data.kline.length === 0) {
    return (
      <div className="w-full h-[200px] bg-bg-secondary border border-border-primary rounded-lg flex items-center justify-center">
        <div className="text-text-muted text-xs font-mono">NO DATA AVAILABLE</div>
      </div>
    );
  }

  const chartData = data?.kline || data?.klines || data || [];

  // 提取日期、收盘价和成交额
  const dates = chartData.map((item: any) => item.trade_date);
  const values = chartData.map((item: any) => item.close);
  const amounts = chartData.map((item: any) => item.amount || 0);

  // 计算涨跌 - 使用设计系统颜色
  const getColor = (index: number) => {
    if (index === 0) return '#1861ff'; // brand-blue
    return values[index] >= values[index - 1] ? '#ff4d4f' : '#00c087'; // up: down
  };

  const option = {
    backgroundColor: 'transparent',
    animation: true,
    grid: [
      {
        left: '3%',
        right: '3%',
        top: '10%',
        height: '55%',
      },
      {
        left: '3%',
        right: '3%',
        top: '72%',
        height: '18%',
      },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
      },
      backgroundColor: '#1c2127',
      borderColor: '#30363d',
      textStyle: {
        color: '#d1d1d1',
        fontSize: 11,
      },
      formatter: (params: any) => {
        const dataIndex = params[0].dataIndex;
        const date = dates[dataIndex];
        const close = values[dataIndex];
        const amount = amounts[dataIndex];

        let color = '#1861ff';
        if (dataIndex > 0) {
          color = values[dataIndex] >= values[dataIndex - 1] ? '#ff4d4f' : '#00c087';
        }

        return `
          <div style="font-family: monospace; font-size: 11px;">
            <div style="color: #8a8d91; margin-bottom: 4px;">${date}</div>
            <div style="font-weight: bold; margin-bottom: 2px;">
              指数: <span style="color: ${color};">${close.toFixed(2)}</span>
            </div>
            <div style="color: #8a8d91;">
              成交额: <span style="color: #ffb11a;">${(amount / 100000000).toFixed(0)} 亿</span>
            </div>
          </div>
        `;
      },
    },
    xAxis: [
      {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: {
          lineStyle: { color: '#1e2226' },
        },
        axisLabel: {
          color: '#8a8d91',
          fontSize: 9,
          fontFamily: 'monospace',
          rotate: 0,
          interval: 'auto',
          hideOverlap: true,
        },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: dates,
        boundaryGap: false,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        axisLine: {
          lineStyle: { color: '#1e2226' },
        },
        axisLabel: {
          color: '#8a8d91',
          fontSize: 9,
          fontFamily: 'monospace',
          formatter: (value: number) => value.toFixed(0),
        },
        axisTick: { show: false },
        splitLine: {
          lineStyle: {
            color: '#1e2226',
            type: 'dashed',
          },
        },
      },
      {
        scale: true,
        gridIndex: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: 0,
        end: 100,
      },
      {
        show: false,
        xAxisIndex: [0, 1],
        type: 'slider',
        start: 0,
        end: 100,
      },
    ],
    series: [
      {
        name: '国证A指',
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'none',
        lineStyle: {
          width: 2,
          color: '#1861ff',
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(24, 97, 255, 0.2)' },
              { offset: 1, color: 'rgba(24, 97, 255, 0)' },
            ],
          },
        },
      },
      {
        name: '成交额',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: amounts.map((amount: number, i: number) => ({
          value: amount,
          itemStyle: {
            color: getColor(i),
          },
        })),
        barWidth: '60%',
      },
    ],
  };

  return (
    <div className="w-full h-[200px] bg-bg-secondary border border-border-primary rounded-lg overflow-hidden">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-primary/50 bg-bg-tertiary/30">
        <div className="flex items-center gap-1.5">
          <div className="w-0.5 h-3 bg-brand-blue shrink-0"></div>
          <h3 className="text-text-primary text-xs font-bold">
            国证A指走势
          </h3>
          <LineChart size={11} className="text-text-secondary ml-0.5" />
        </div>
        <div className="text-xs text-text-secondary font-mono">
          近 {days} 天
        </div>
      </div>

      {/* 图表区域 */}
      <div className="p-2 h-[calc(100%-37px)]">
        <ReactECharts
          option={option}
          style={{ height: '100%', width: '100%' }}
          notMerge={true}
          lazyUpdate={true}
        />
      </div>
    </div>
  );
}
