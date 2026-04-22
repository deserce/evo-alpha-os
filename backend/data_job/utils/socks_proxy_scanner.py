"""
SOCKS 代理扫描器 - 测试 Clash Verge SOCKS 端口可用性
扫描 42000-42051 端口范围，找出可用的 SOCKS 代理
"""

import concurrent.futures
import time
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SocksProxyScanner:
    """SOCKS 代理扫描器"""

    def __init__(
        self,
        host='127.0.0.1',
        port_range=(42000, 42051),
        test_url='https://push2his.eastmoney.com',
        timeout=5,
        max_workers=20
    ):
        """
        初始化扫描器

        Args:
            host: 代理主机
            port_range: 端口范围 (start, end)
            test_url: 测试URL
            timeout: 超时时间（秒）
            max_workers: 最大并发数
        """
        self.host = host
        self.port_start, self.port_end = port_range
        self.test_url = test_url
        self.timeout = timeout
        self.max_workers = max_workers

        # 结果存储
        self.results = []
        self.available_proxies = []

    def test_single_proxy(self, port: int) -> Tuple[int, bool, float, str]:
        """
        测试单个代理端口

        Args:
            port: 端口号

        Returns:
            (port, is_available, latency, error)
        """
        proxy_url = f"socks5://{self.host}:{port}"

        start_time = time.time()

        try:
            # 安装 socks 代理支持
            try:
                from requests.exceptions import ProxyError
                # 使用 SOCKS 代理
                # 注意：需要 pip install 'requests[socks]'
                import socks
            except ImportError:
                return (port, False, 0, "缺少 pysocks 库，请运行: pip3 install pysocks")

            response = requests.get(
                self.test_url,
                proxies={
                    'http': proxy_url,
                    'https': proxy_url
                },
                timeout=self.timeout
            )

            latency = time.time() - start_time

            if response.status_code == 200:
                return (port, True, latency, "")
            else:
                return (port, False, latency, f"HTTP {response.status_code}")

        except Exception as e:
            latency = time.time() - start_time
            error_msg = str(e).split(':')[0] if ':' in str(e) else str(e)
            return (port, False, latency, error_msg[:50])

    def scan(self) -> List[Dict]:
        """
        扫描所有端口

        Returns:
            可用代理列表 [{'port': 42001, 'latency': 1.2}, ...]
        """
        total_ports = self.port_end - self.port_start + 1

        logger.info("=" * 60)
        logger.info(f"🔍 开始扫描 SOCKS 代理端口 {self.port_start}-{self.port_end}")
        logger.info(f"   共 {total_ports} 个端口")
        logger.info(f"   并发数: {self.max_workers}")
        logger.info("=" * 60)

        results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有测试任务
            future_to_port = {
                executor.submit(self.test_single_proxy, port): port
                for port in range(self.port_start, self.port_end + 1)
            }

            # 收集结果
            completed = 0
            for future in concurrent.futures.as_completed(future_to_port):
                port = future_to_port[future]
                completed += 1

                try:
                    port, is_available, latency, error = future.result()

                    result = {
                        'port': port,
                        'available': is_available,
                        'latency': latency,
                        'error': error
                    }
                    results.append(result)

                    # 实时显示进度
                    if is_available:
                        logger.info(f"✅ [{completed}/{total_ports}] 端口 {port}: 可用 (延迟: {latency:.2f}s)")
                    else:
                        # 只显示有错误信息的
                        if error and error != "缺少 pysocks 库":
                            logger.debug(f"❌ [{completed}/{total_ports}] 端口 {port}: {error}")

                except Exception as e:
                    logger.debug(f"❌ [{completed}/{total_ports}] 端口 {port}: 测试异常 - {e}")

        # 统计结果
        available_count = sum(1 for r in results if r['available'])
        logger.info("\n" + "=" * 60)
        logger.info(f"📊 扫描完成")
        logger.info(f"   总计: {len(results)} 个端口")
        logger.info(f"   可用: {available_count} 个")
        logger.info(f"   不可用: {len(results) - available_count} 个")
        logger.info("=" * 60)

        # 提取可用代理并按延迟排序
        self.available_proxies = [
            {'port': r['port'], 'latency': r['latency']}
            for r in results
            if r['available']
        ]
        self.available_proxies.sort(key=lambda x: x['latency'])

        if self.available_proxies:
            logger.info(f"\n✅ 可用代理列表（按延迟排序）:")
            for i, proxy in enumerate(self.available_proxies, 1):
                logger.info(f"   {i}. 端口 {proxy['port']} - 延迟: {proxy['latency']:.2f}s")
        else:
            logger.warning(f"\n⚠️  没有找到可用的代理！")

        return results

    def save_results(self, output_file: str = None):
        """
        保存扫描结果

        Args:
            output_file: 输出文件路径
        """
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = f"data_job/data/socks_proxy_scan_{timestamp}.json"

        # 确保目录存在
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        # 保存结果
        data = {
            'scan_time': datetime.now().isoformat(),
            'host': self.host,
            'port_range': f"{self.port_start}-{self.port_end}",
            'available_proxies': self.available_proxies,
            'total_available': len(self.available_proxies)
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"\n💾 结果已保存到: {output_file}")


def main():
    """主函数"""
    scanner = SocksProxyScanner(
        host='127.0.0.1',
        port_range=(42000, 42051),
        test_url='https://push2his.eastmoney.com',
        timeout=5,
        max_workers=20
    )

    # 扫描
    scanner.scan()

    # 保存结果
    scanner.save_results()

    return scanner.available_proxies


if __name__ == "__main__":
    proxies = main()
