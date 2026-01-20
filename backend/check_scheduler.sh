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

# 检查进程是否运行
if ps -p 4490 > /dev/null 2>&1; then
    echo -e "${GREEN}✅ 调度器正在运行 (PID: 4490)${NC}"
else
    echo -e "${RED}❌ 调度器未运行${NC}"
    echo ""
    echo "启动命令:"
    echo "  cd /Users/deserce/Desktop/EvoAlpha-OS/backend"
    echo "  nohup python3 auto_pipeline.py --mode schedule > /tmp/auto_pipeline.log 2>&1 &"
    exit 1
fi

echo ""
echo "📅 已配置的定时任务:"
echo "  - 每日流水线: 工作日 15:30"
echo "  - 季度流水线: 每季度15号 08:00"
echo ""

# 显示最近日志
echo "📝 最近日志 (最后20行):"
echo -e "${BLUE}========================================${NC}"
tail -20 "$SCHEDULER_LOG"
echo ""

# 检查下次执行时间
echo "⏰ 下次执行时间:"
echo -e "${BLUE}========================================${NC}"
grep "Next run" "$SCHEDULER_LOG" | tail -2
echo ""

echo -e "${BLUE}========================================${NC}"
echo "💡 提示:"
echo "  - 查看完整日志: tail -f $SCHEDULER_LOG"
echo "  - 停止调度器: kill 4490"
echo "  - 重启调度器: ./restart_scheduler.sh"
echo -e "${BLUE}========================================${NC}"
