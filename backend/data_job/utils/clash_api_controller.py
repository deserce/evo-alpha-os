"""
Clash Verge API 控制器
用于控制 Clash 切换节点、获取状态等
"""

import requests
import time
import logging
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ClashAPIController:
    """Clash API 控制器"""

    def __init__(
        self,
        api_host='127.0.0.1',
        api_port=9090,
        proxy_port=7890  # Clash mixed-port
    ):
        """
        初始化 Clash API 控制器

        Args:
            api_host: API 主机
            api_port: API 端口
            proxy_port: 代理端口（用于 HTTP 请求）
        """
        self.api_host = api_host
        self.api_port = api_port
        self.proxy_port = proxy_port

        self.api_base = f"http://{api_host}:{api_port}"
        self.proxy_url = f"http://{api_host}:{proxy_port}"

        # 会话
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json'
        })

        logger.info(f"✅ Clash API 控制器初始化完成")
        logger.info(f"   API地址: {self.api_base}")
        logger.info(f"   代理端口: {self.proxy_port}")

    def get_proxies(self) -> List[Dict]:
        """
        获取所有代理节点

        Returns:
            代理节点列表
        """
        try:
            response = self.session.get(f"{self.api_base}/proxies")
            response.raise_for_status()
            data = response.json()

            proxies = data.get('proxies', {})
            logger.info(f"✅ 获取到 {len(proxies)} 个代理节点")

            return [
                {
                    'name': name,
                    'type': proxy.get('type', ''),
                    'alive': proxy.get('alive', False),
                    'extra': proxy
                }
                for name, proxy in proxies.items()
            ]

        except Exception as e:
            logger.error(f"❌ 获取代理列表失败: {e}")
            return []

    def get_proxy_groups(self) -> List[Dict]:
        """
        获取代理组

        Returns:
            代理组列表
        """
        try:
            response = self.session.get(f"{self.api_base}/proxies")
            response.raise_for_status()
            data = response.json()

            groups = data.get('proxy-groups', [])
            logger.info(f"✅ 获取到 {len(groups)} 个代理组")

            return groups

        except Exception as e:
            logger.error(f"❌ 获取代理组失败: {e}")
            return []

    def get_rules(self) -> List[Dict]:
        """
        获取规则

        Returns:
            规则列表
        """
        try:
            response = self.session.get(f"{self.api_base}/rules")
            response.raise_for_status()
            data = response.json()

            rules = data.get('rules', [])
            logger.info(f"✅ 获取到 {len(rules)} 条规则")

            return rules

        except Exception as e:
            logger.error(f"❌ 获取规则失败: {e}")
            return []

    def switch_proxy_group(
        self,
        group_name: str = '一分机场',
        proxy_name: Optional[str] = None
    ) -> bool:
        """
        切换代理组的节点

        Args:
            group_name: 代理组名称
            proxy_name: 节点名称（None 表示不指定，由 Clash 自动选择）

        Returns:
            是否成功
        """
        try:
            # 获取代理组
            groups = self.get_proxy_groups()
            target_group = None

            for group in groups:
                if group['name'] == group_name:
                    target_group = group
                    break

            if not target_group:
                logger.error(f"❌ 找不到代理组: {group_name}")
                return False

            # 构造切换数据
            if proxy_name:
                # 切换到指定节点
                data = {
                    'name': proxy_name
                }
                logger.info(f"🔄 切换代理组 '{group_name}' 到节点 '{proxy_name}'")
            else:
                # 只切换代理组，不指定具体节点
                logger.info(f"🔄 切换代理组 '{group_name}'（保持当前节点或自动选择）")
                return True

            # 执行切换
            url = f"{self.api_base}/proxies/{target_group['type']}"
            response = self.session.put(url, json=data)
            response.raise_for_status()

            logger.info("✅ 节点切换成功")
            time.sleep(1)  # 等待切换生效

            return True

        except Exception as e:
            logger.error(f"❌ 切换节点失败: {e}")
            return False

    def get_current_proxy(self, group_name: str = '一分机场') -> Optional[str]:
        """
        获取代理组当前使用的节点

        Args:
            group_name: 代理组名称

        Returns:
            当前节点名称
        """
        try:
            groups = self.get_proxy_groups()

            for group in groups:
                if group['name'] == group_name:
                    current = group.get('now', '')
                    logger.info(f"📍 代理组 '{group_name}' 当前节点: {current}")
                    return current

            logger.warning(f"⚠️ 找不到代理组: {group_name}")
            return None

        except Exception as e:
            logger.error(f"❌ 获取当前节点失败: {e}")
            return None

    def test_current_proxy_for_target(self, group_name: str = '一分机场') -> bool:
        """
        测试当前代理是否能访问目标网站

        Args:
            group_name: 代理组名称

        Returns:
            是否可用
        """
        logger.info(f"🧪 测试当前代理...")

        # 使用代理访问目标网站
        try:
            response = self.session.get(
                'https://push2his.eastmoney.com/api/qt/stock/kline/get',
                params={
                    'secid': '0.000001',
                    'beg': '20240301',
                    'end': '20240305',
                    'klt': '101',
                    'fqt': '1'
                },
                proxies={
                    'http': self.proxy_url,
                    'https': self.proxy_url
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                if data and data.get('data'):
                    klines = data['data'].get('klines', [])
                    logger.info(f"✅ 当前代理可用（获取到 {len(klines)} 条数据）")
                    return True

            logger.warning(f"⚠️ 状态码: {response.status_code}")
            return False

        except Exception as e:
            logger.warning(f"❌ 测试失败: {str(e)[:80]}")
            return False

    def test_all_nodes_for_target(
        self,
        group_name: str = '一分机场',
        max_concurrent=5
    ) -> List[Dict]:
        """
        测试所有节点对目标网站的可用性

        Args:
            group_name: 代理组名称
            max_concurrent: 最大并发数

        Returns:
            可用节点列表 [{'name': '节点名', 'latency': 延迟}, ...]
        """
        logger.info("=" * 60)
        logger.info(f"🧪 测试 '{group_name}' 中所有节点")
        logger.info("=" * 60)

        # 获取代理组
        groups = self.get_proxy_groups()
        target_group = None

        for group in groups:
            if group['name'] == group_name:
                target_group = group
                break

        if not target_group:
            logger.error(f"❌ 找不到代理组: {group_name}")
            return []

        # 提取节点列表（排除其他代理组）
        test_proxies = []
        for proxy_name in target_group.get('proxies', []):
            # 跳过代理组本身
            if proxy_name not in ['自动选择', '故障转移', 'DIRECT']:
                test_proxies.append(proxy_name)

        logger.info(f"📋 将测试 {len(test_proxies)} 个节点（并发: {max_concurrent}）")
        logger.info("")

        # 测试函数
        def test_node(proxy_name: str) -> Dict:
            """测试单个节点"""
            start_time = time.time()

            try:
                # 切换到该节点
                if not self.switch_proxy_group(group_name, proxy_name):
                    return {'name': proxy_name, 'available': False, 'error': '切换失败'}

                time.sleep(1)  # 等待切换生效

                # 测试访问
                response = self.session.get(
                    'https://push2his.eastmoney.com/api/qt/stock/kline/get',
                    params={
                        'secid': '0.000001',
                        'beg': '20240301',
                        'end': '20240305',
                        'klt': '101',
                        'fqt': '1'
                    },
                    proxies={
                        'http': self.proxy_url,
                        'https': self.proxy_url_url,
                    },
                    timeout=15
                )

                latency = time.time() - start_time

                if response.status_code == 200:
                    data = response.json()
                    if data and data.get('data'):
                        klines = data['data'].get('klines', [])
                        logger.info(f"✅ [{proxy_name[:20]:20s}] 可用 ({latency:.2f}s, {len(klines)}条数据)")
                        return {'name': proxy_name, 'available': True, 'latency': latency}

                logger.debug(f"❌ [{proxy_name[:20]:20s}] 状态码: {response.status_code}")
                return {'name': proxy_name, 'available': False, 'error': f'HTTP{response.status_code}'}

            except Exception as e:
                error_str = str(e)[:50]
                logger.debug(f"❌ [{proxy_name[:20]:20s}] {error_str}")
                return {'name': proxy_name, 'available': False, 'error': error_str}

        # 并发测试
        available_nodes = []

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(test_node, proxy_name): proxy_name
                for proxy_name in test_proxies
            }

            completed = 0
            total = len(test_proxies)

            for future in as_completed(futures):
                proxy_name = futures[future]
                completed += 1

                try:
                    result = future.result(timeout=30)
                    if result['available']:
                        available_nodes.append(result)
                except Exception as e:
                    logger.debug(f"❌ [{proxy_name}] 测试超时或异常")

                # 进度
                if completed % 5 == 0:
                    logger.info(f"   进度: {completed}/{total}")

        # 按延迟排序
        available_nodes.sort(key=lambda x: x['latency'])

        logger.info("\n" + "=" * 60)
        logger.info(f"📊 测试完成")
        logger.info("=" * 60)
        logger.info(f"可用节点: {len(available_nodes)}/{len(test_proxies)}")

        if available_nodes:
            logger.info(f"\n✅ 推荐使用（按延迟排序）:")
            for i, node in enumerate(available_nodes[:10], 1):
                logger.info(f"   {i}. {node['name'][:30]:30s} - {node['latency']:.2f}s")

        # 保存结果
        self.save_test_results(available_nodes)

        return available_nodes

    def save_test_results(self, available_nodes: List[Dict]):
        """保存测试结果"""
        import json
        from datetime import datetime
        from pathlib import Path

        output_dir = Path("data_job/data/clash_proxy_tests")
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = output_dir / f"proxy_test_{timestamp}.json"

        data = {
            'test_time': datetime.now().isoformat(),
            'proxy_port': self.proxy_port,
            'available_nodes': available_nodes,
            'total_available': len(available_nodes)
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"\n💾 测试结果已保存: {output_file}")

    def print_status(self):
        """打印 Clash 状态"""
        try:
            # 获取当前节点
            current = self.get_current_proxy()
            logger.info(f"📍 当前节点: {current if current else 'N/A'}")

            # 测试连接
            if self.test_current_proxy_for_target():
                logger.info("✅ 代理连接正常")
            else:
                logger.warning("⚠️ 代理连接异常")

        except Exception as e:
            logger.error(f"❌ 状态检查失败: {e}")


# 测试
if __name__ == "__main__":
    controller = ClashAPIController()

    # 打印状态
    controller.print_status()

    # 测试所有节点
    print("\n" + "=" * 60)
    print("是否测试所有节点？（这会需要较长时间）")
    print("=" * 60)

    # 自动测试（取消注释以运行）
    # available_nodes = controller.test_all_nodes_for_target(max_concurrent=5)
