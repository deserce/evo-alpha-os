#!/usr/bin/env python3
"""
Clash 代理测速工具
测试所有代理端口速度并显示结果
"""

import sys
from pathlib import Path

# 添加路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from data_job.collectors.clash_proxy_collector import ClashProxyRotator

def main():
    """主函数"""
    print("=" * 80)
    print("🚀 Clash Verge 代理测速工具")
    print("=" * 80)

    # 创建代理轮询器
    rotator = ClashProxyRotator(
        base_port=42000,
        proxy_count=52,
        max_latency_ms=1000,  # 超过 1 秒的代理将被过滤
    )

    # 测速
    results = rotator.speed_test_all_proxies()

    # 显示最快代理
    print("\n" + "=" * 80)
    print("🏆 最快代理 TOP 10")
    print("=" * 80)

    fast_proxies = sorted(
        [(port, latency) for port, latency in results.items() if latency > 0],
        key=lambda x: x[1]
    )[:10]

    for i, (port, latency) in enumerate(fast_proxies, 1):
        print(f"{i}. 端口 {port}: {latency:.0f}ms")

    # 保存结果
    print("\n" + "=" * 80)
    print(f"📊 测速结果摘要")
    print("=" * 80)

    fast_count = sum(1 for v in results.values() if 0 < v <= 1000)
    slow_count = sum(1 for v in results.values() if v > 1000)
    failed_count = sum(1 for v in results.values() if v <= 0)

    print(f"快速代理 (≤1000ms): {fast_count} 个")
    print(f"慢速代理 (>1000ms):  {slow_count} 个")
    print(f"失败代理：{failed_count} 个")
    print(f"可用率：{fast_count/len(results)*100:.1f}%")

    # 保存最快的 20 个端口
    if fast_proxies:
        fast_ports = [str(port) for port, _ in fast_proxies[:20]]
        print(f"\n推荐使用的快速端口：{', '.join(fast_ports)}")

if __name__ == "__main__":
    main()
