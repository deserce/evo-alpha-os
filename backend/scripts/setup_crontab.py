#!/usr/bin/env python3
"""
EvoAlpha OS 定时任务设置工具

用法:
    python3 setup_crontab.py        # 安装定时任务
    python3 setup_crontab.py --uninstall  # 卸载定时任务
    python3 setup_crontab.py --list  # 查看当前定时任务
"""

import sys
import os
import subprocess
import argparse
from pathlib import Path

# 当前脚本目录
SCRIPT_DIR = Path(__file__).parent.absolute()
BACKEND_DIR = SCRIPT_DIR.parent

# 定时任务配置（使用mootdx数据源以获得最快速度）
CRON_JOB = "30 15 * * 1-5 cd {BACKEND_DIR} && python3 scripts/run_daily_quant_flow.py --source mootdx >> logs/daily_flow.log 2>&1".format(
    BACKEND_DIR=BACKEND_DIR
)

# 日志目录
LOG_DIR = BACKEND_DIR / "logs"


def setup_crontab():
    """安装定时任务"""
    # 确保日志目录存在
    LOG_DIR.mkdir(exist_ok=True)

    # 获取当前crontab
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    current_cron = result.stdout if result.returncode == 0 else ""

    # 检查是否已存在
    if 'run_daily_quant_flow.py' in current_cron:
        print("⚠️  定时任务已存在")
        answer = input("是否要覆盖? (y/N): ")
        if answer.lower() != 'y':
            print("已取消")
            return

        # 移除旧任务
        lines = [line for line in current_cron.split('\n') if 'run_daily_quant_flow.py' not in line]
        current_cron = '\n'.join(lines)

    # 添加新任务
    marker_start = "# ===== EvoAlpha OS 定时任务开始 ====="
    marker_end = "# ===== EvoAlpha OS 定时任务结束 ====="

    new_cron_entry = f"\n{marker_start}\n{CRON_JOB}\n{marker_end}\n"

    # 写入新crontab
    new_cron = current_cron + new_cron

    proc = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE)
    proc.communicate(new_cron.encode())

    if proc.returncode == 0:
        print("✅ 定时任务安装成功!")
        print(f"\n📅 定时任务配置:")
        print(f"   执行时间: 每个工作日 15:30")
        print(f"   执行命令: python3 scripts/run_daily_quant_flow.py --source mootdx")
        print(f"   日志文件: {LOG_DIR}/daily_flow.log")
        print(f"\n💡 使用 'crontab -l' 查看所有定时任务")
    else:
        print("❌ 定时任务安装失败")


def uninstall_crontab():
    """卸载定时任务"""
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    current_cron = result.stdout if result.returncode == 0 else ""

    if 'run_daily_quant_flow.py' not in current_cron:
        print("⚠️  未找到相关定时任务")
        return

    # 移除相关任务
    marker_start = "# ===== EvoAlpha OS 定时任务开始 ====="
    marker_end = "# ===== EvoAlpha OS 定时任务结束 ====="

    lines = current_cron.split('\n')
    new_lines = []
    skip = False

    for line in lines:
        if marker_start in line:
            skip = True
        elif marker_end in line:
            skip = False
        elif not skip:
            new_lines.append(line)

    new_cron = '\n'.join(new_lines)

    # 写入crontab
    proc = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE)
    proc.communicate(new_cron.encode())

    if proc.returncode == 0:
        print("✅ 定时任务已卸载")
    else:
        print("❌ 卸载失败")


def list_crontab():
    """列出当前定时任务"""
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    current_cron = result.stdout if result.returncode == 0 else ""

    print("📅 当前定时任务:")
    print("-" * 60)

    if 'run_daily_quant_flow.py' in current_cron:
        for line in current_cron.split('\n'):
            if 'run_daily_quant_flow.py' in line or 'EvoAlpha OS' in line:
                print(line)
    else:
        print("未找到 EvoAlpha OS 定时任务")
        print("\n💡 运行 'python3 setup_crontab.py' 安装定时任务")


def show_status():
    """显示定时任务状态"""
    print("📊 EvoAlpha OS 定时任务状态")
    print("=" * 60)

    # 检查crontab
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    current_cron = result.stdout if result.returncode == 0 else ""

    if 'run_daily_quant_flow.py' in current_cron:
        print("✅ 定时任务状态: 已安装")

        # 提取任务信息
        for line in current_cron.split('\n'):
            if 'run_daily_quant_flow.py' in line:
                print(f"   {line.strip()}")
    else:
        print("❌ 定时任务状态: 未安装")

    print()
    print(f"📝 工作目录: {BACKEND_DIR}")
    print(f"📋 日志目录: {LOG_DIR}")

    # 检查日志文件
    log_file = LOG_DIR / "daily_flow.log"
    if log_file.exists():
        print(f"📄 日志文件: 存在 ({log_file.stat().st_size} bytes)")

        # 显示最后几行
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
            if lines:
                print("\n📋 最近日志 (最后5行):")
                for line in lines[-5:]:
                    print(f"   {line.rstrip()}")
        except:
            pass
    else:
        print(f"📄 日志文件: 不存在")


def main():
    parser = argparse.ArgumentParser(
        description="EvoAlpha OS 定时任务设置工具"
    )
    parser.add_argument(
        '--uninstall',
        action='store_true',
        help='卸载定时任务'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='列出当前定时任务'
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='显示定时任务状态'
    )

    args = parser.parse_args()

    if args.list:
        list_crontab()
    elif args.status:
        show_status()
    elif args.uninstall:
        uninstall_crontab()
    else:
        setup_crontab()


if __name__ == "__main__":
    main()
