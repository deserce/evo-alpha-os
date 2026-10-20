#!/bin/bash
# ========================================
# EvoAlpha OS - 重启调度器脚本
# ========================================

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="/tmp/auto_pipeline.pid"
LOG_FILE="/tmp/auto_pipeline.log"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}重启自动化交易流水线调度器${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 停止旧进程
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo -e "${YELLOW}⏹️  停止旧调度器 (PID: $OLD_PID)...${NC}"
        kill "$OLD_PID"
        sleep 2
    fi
fi

# 清理旧的PID文件
rm -f "$PID_FILE"

# 启动新进程
echo -e "${GREEN}🚀 启动新调度器...${NC}"
cd "$SCRIPT_DIR" || exit 1
nohup python3 scripts/schedule_daily_data_workflow.py > "$LOG_FILE" 2>&1 &
NEW_PID=$!

echo $NEW_PID > "$PID_FILE"

sleep 2

echo ""
echo -e "${GREEN}✅ 调度器已重启 (新PID: $NEW_PID)${NC}"
echo ""
echo "📝 查看日志: tail -f $LOG_FILE"
echo "📊 检查状态: ./check_scheduler.sh"
echo -e "${BLUE}========================================${NC}"
