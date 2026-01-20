#!/bin/bash
# ========================================
# EvoAlpha OS - 重启调度器脚本
# ========================================

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}重启自动化交易流水线调度器${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 停止旧进程
if ps -p 4490 > /dev/null 2>&1; then
    echo -e "${YELLOW}⏹️  停止旧调度器 (PID: 4490)...${NC}"
    kill 4490
    sleep 2
fi

# 清理旧的PID文件
rm -f /tmp/auto_pipeline.pid

# 启动新进程
echo -e "${GREEN}🚀 启动新调度器...${NC}"
cd /Users/deserce/Desktop/EvoAlpha-OS/backend
nohup python3 auto_pipeline.py --mode schedule > /tmp/auto_pipeline.log 2>&1 &
NEW_PID=$!

echo $NEW_PID > /tmp/auto_pipeline.pid

sleep 2

echo ""
echo -e "${GREEN}✅ 调度器已重启 (新PID: $NEW_PID)${NC}"
echo ""
echo "📝 查看日志: tail -f /tmp/auto_pipeline.log"
echo "📊 检查状态: ./check_scheduler.sh"
echo -e "${BLUE}========================================${NC}"
