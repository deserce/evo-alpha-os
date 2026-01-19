#!/bin/bash
# EvoAlpha OS - 启动定时采集调度器

echo "🚀 EvoAlpha OS - 定时采集调度器"
echo "================================"
echo "⏰ 启动时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "📋 已配置的定时任务:"
echo "  - 📈 每日采集: 工作日 15:30 (收盘后)"
echo "  - 📅 每月采集: 每月1号 08:00"
echo "  - 💰 每季度采集: 每季度15号 08:00"
echo ""
echo "💡 提示: 按 Ctrl+C 停止调度器"
echo "================================"
echo ""

# 检查是否安装了 APScheduler
python -c "import apscheduler" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "❌ APScheduler 未安装"
    echo "请运行: pip install apscheduler"
    exit 1
fi

# 进入backend目录
cd "$(dirname "$0")" || exit 1

# 启动调度器
python -m data_job.utils.scheduler --mode schedule
