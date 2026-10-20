#!/bin/bash
# EvoAlpha OS - 每日数据采集便捷脚本

echo "🚀 EvoAlpha OS - 每日数据采集"
echo "================================"
echo "⏰ 开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 进入backend目录
cd "$(dirname "$0")" || exit 1

# 运行唯一生产入口：采集 + RPS + P0 强度/结构/生命周期
python scripts/run_daily_data_workflow.py

echo ""
echo "✅ 每日数据采集完成"
echo "⏰ 结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
