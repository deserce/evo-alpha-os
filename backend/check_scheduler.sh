#!/bin/bash
# ========================================
# EvoAlpha OS - 定时调度器监控脚本
# ========================================

SCHEDULER_LOG="/tmp/auto_pipeline.log"
PID_FILE="/tmp/auto_pipeline.pid"

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}自动化交易流水线调度器监控${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
else
    PID=""
fi

# 检查进程是否运行
if [ -n "$PID" ] && ps -p "$PID" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ 调度器正在运行 (PID: $PID)${NC}"
else
    echo -e "${RED}❌ 调度器未运行${NC}"
    echo ""
    echo "启动命令:"
    echo "  cd $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo "  nohup python3 scripts/schedule_daily_data_workflow.py > /tmp/auto_pipeline.log 2>&1 &"
    exit 1
fi

echo ""
echo "📅 已配置的定时任务:"
echo "  - 每日生产流水线: 工作日 16:00"
echo ""

# 显示最近日志
echo "📝 最近日志 (最后20行):"
echo -e "${BLUE}========================================${NC}"
if [ -f "$SCHEDULER_LOG" ]; then
    tail -20 "$SCHEDULER_LOG"
else
    echo "暂无日志: $SCHEDULER_LOG"
fi
echo ""

# 检查下次执行时间
echo "⏰ 下次执行时间:"
echo -e "${BLUE}========================================${NC}"
if [ -f "$SCHEDULER_LOG" ]; then
    grep "Next run" "$SCHEDULER_LOG" | tail -2
fi
echo ""

echo -e "${BLUE}========================================${NC}"
echo "💡 提示:"
echo "  - 查看完整日志: tail -f $SCHEDULER_LOG"
echo "  - 停止调度器: kill $PID"
echo "  - 重启调度器: ./restart_scheduler.sh"
echo -e "${BLUE}========================================${NC}"
