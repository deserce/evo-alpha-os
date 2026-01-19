#!/bin/bash
# EvoAlpha OS - 初始化数据采集（首次全量采集）

echo "🚀 EvoAlpha OS - 初始化数据采集"
echo "================================"
echo "⏰ 开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "📋 采集步骤:"
echo "  Step 1/5: 基础数据 (15-25分钟)"
echo "  Step 2/5: 市场数据 (15-25分钟)"
echo "  Step 3/5: 财务数据 (2-3.5小时)"
echo "  Step 4/5: K线数据 (3.5-4.5小时) ⭐ 核心数据，耗时最长"
echo "  Step 5/5: 舆情数据 (10-20分钟)"
echo ""
echo "⏱️  总预计耗时: 7-9小时（首次采集）"
echo ""
echo "💡 提示:"
echo "  - 这是首次全量采集，时间较长"
echo "  - 建议: 在空闲时间运行，或使用 --step 参数分步执行"
echo "  - 如需中断: 按 Ctrl+C（已采集的数据会保存）"
echo "  - 分步执行示例: ./init_data.sh --step 1 (只执行Step 1)"
echo "================================"
echo ""

# 确认开始
read -p "是否开始初始化采集？(y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ 已取消"
    exit 1
fi

# 进入backend目录
cd "$(dirname "$0")" || exit 1

# 运行初始化采集
python data_job/scripts/init_data_collection.py "$@"

echo ""
echo "✅ 初始化数据采集完成"
echo "⏰ 结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "💡 下一步:"
echo "  1. 查看采集的数据"
echo "  2. 启动定时调度器: ./start_scheduler.sh"
echo "  3. 或手动运行每日采集: ./run_daily_collection.sh"
