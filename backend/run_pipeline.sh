#!/bin/bash
# ========================================
# EvoAlpha OS - 自动化交易流水线启动脚本
# ========================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}EvoAlpha OS - 自动化交易流水线${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ 错误: 未找到 python3${NC}"
    exit 1
fi

# 检查虚拟环境
if [ -d "venv" ]; then
    echo -e "${GREEN}✅ 激活虚拟环境...${NC}"
    source venv/bin/activate
fi

# 解析命令行参数
MODE="schedule"
if [ "$1" == "--mode" ] && [ -n "$2" ]; then
    MODE=$2
fi

echo -e "${YELLOW}📋 运行模式: $MODE${NC}"
echo ""

case $MODE in
    schedule)
        echo -e "${GREEN}🚀 启动定时调度器...${NC}"
        echo -e "${YELLOW}💡 提示: 按 Ctrl+C 停止调度器${NC}"
        echo ""
        python3 auto_pipeline.py --mode schedule
        ;;
    daily)
        echo -e "${GREEN}🚀 立即运行每日自动化流水线...${NC}"
        echo ""
        python3 auto_pipeline.py --mode daily
        ;;
    quarterly)
        echo -e "${GREEN}🚀 立即运行季度自动化流水线...${NC}"
        echo ""
        python3 auto_pipeline.py --mode quarterly
        ;;
    *)
        echo -e "${RED}❌ 错误: 未知的模式 '$MODE'${NC}"
        echo ""
        echo "使用方法:"
        echo "  ./run_pipeline.sh              # 启动定时调度器（默认）"
        echo "  ./run_pipeline.sh --mode daily # 立即运行每日流水线"
        echo "  ./run_pipeline.sh --mode quarterly # 立即运行季度流水线"
        exit 1
        ;;
esac

# 捕获退出码
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ 执行完成${NC}"
else
    echo ""
    echo -e "${RED}❌ 执行失败 (退出码: $EXIT_CODE)${NC}"
fi

exit $EXIT_CODE
