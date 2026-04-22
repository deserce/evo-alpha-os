#!/bin/bash
#
# EvoAlpha OS 每日量化全流程定时任务脚本
#
# 使用方法:
#   1. 将此文件复制到 ~/.local/bin/ 或 ~/bin/ 目录
#   2. 添加执行权限: chmod +x run_daily_quant.sh
#   3. 添加到 crontab: crontab -e
#
# Crontab 配置示例 (每个工作日15:30执行):
#   30 15 * * 1-5 /path/to/backend/scripts/run_daily_quant.sh >> /path/to/logs/daily_flow.log 2>&1
#

# 设置工作目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

cd "$BACKEND_DIR" || exit 1

# 激活虚拟环境（如果使用）
# if [ -d "venv" ]; then
#     source venv/bin/activate
# fi

# 记录开始时间
echo "=========================================="
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "工作目录: $(pwd)"
echo "=========================================="

# 执行量化全流程（使用mootdx数据源获得最快速度）
python3 scripts/run_daily_quant_flow.py --source mootdx

EXIT_CODE=$?

# 记录结束时间
echo "=========================================="
echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "退出码: $EXIT_CODE"
echo "=========================================="

exit $EXIT_CODE
