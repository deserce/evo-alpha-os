#!/bin/bash
# ========================================
# EvoAlpha OS - 自动化流水线监控通知脚本
# ========================================

# 任务ID和输出文件
TASK_ID="b8e3ce6"
OUTPUT_FILE="/private/tmp/claude/-Users-deserce-Desktop-EvoAlpha-OS/tasks/${TASK_ID}.output"
LOG_FILE="/tmp/pipeline_monitor.log"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}自动化流水线监控${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "📊 监控任务: ${TASK_ID}"
echo "📝 日志文件: ${OUTPUT_FILE}"
echo ""
echo "⏳ 正在监控流水线执行进度..."
echo "💡 完成后将发送桌面通知"
echo ""

# 检查输出文件是否存在
if [ ! -f "$OUTPUT_FILE" ]; then
    echo "❌ 错误: 输出文件不存在"
    exit 1
fi

# 监控循环
check_interval=60  # 每60秒检查一次
completed=false

while [ "$completed" = false ]; do
    # 检查是否完成
    if grep -q "✅ 每日自动化交易流水线完成" "$OUTPUT_FILE" 2>/dev/null; then
        completed=true
        SUCCESS=true
    elif grep -q "❌ RPS计算失败，跳过策略选股" "$OUTPUT_FILE" 2>/dev/null; then
        completed=true
        SUCCESS=false
    fi

    # 如果未完成，显示当前进度并等待
    if [ "$completed" = false ]; then
        # 提取当前步骤
        CURRENT_STEP=$(tail -20 "$OUTPUT_FILE" 2>/dev/null | grep -E "Step [0-9]/3" | tail -1)
        CURRENT_TASK=$(tail -20 "$OUTPUT_FILE" 2>/dev/null | grep -E "▶️  正在运行:" | tail -1)

        echo -n "$(date '+%H:%M:%S') - "
        if [ -n "$CURRENT_STEP" ]; then
            echo -e "${GREEN}$CURRENT_STEP${NC}"
        else
            echo "运行中..."
        fi

        if [ -n "$CURRENT_TASK" ]; then
            echo "  $CURRENT_TASK"
        fi

        sleep $check_interval
    fi
done

# 流水线完成
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}流水线执行完成${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 提取结果摘要
if [ "$SUCCESS" = true ]; then
    echo -e "${GREEN}✅ 状态: 成功${NC}"

    # 发送成功通知
    osascript -e 'display notification "✅ 自动化交易流水线执行成功！" with title "EvoAlpha OS" sound name "Glass"'

    # 提取关键信息
    END_TIME=$(tail -50 "$OUTPUT_FILE" | grep "结束时间:" | tail -1)
    if [ -n "$END_TIME" ]; then
        echo "$END_TIME"
    fi

    # 检查选股结果
    if grep -q "✅ MRGC策略选股完成" "$OUTPUT_FILE"; then
        echo -e "${GREEN}📊 策略选股: 已完成${NC}"
        echo "💡 请查看 quant_preselect_results 表获取选股结果"
    fi

else
    echo -e "${YELLOW}⚠️  状态: 部分失败${NC}"

    # 发送失败通知
    osascript -e 'display notification "⚠️ 自动化交易流水线部分失败，请查看日志" with title "EvoAlpha OS" sound name "Basso"'

    # 提取错误信息
    echo ""
    echo "错误信息:"
    tail -30 "$OUTPUT_FILE" | grep -E "❌|错误|失败" | head -5
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo "📝 完整日志: $OUTPUT_FILE"
echo -e "${BLUE}========================================${NC}"
