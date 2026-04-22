"""
使用 curl 获取数据 - 绕过 Python requests 的代理问题
"""

import subprocess
import json
import logging
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)


class CurlDataFetcher:
    """使用 curl 获取数据"""

    def __init__(self, proxy_port=7897):
        """
        初始化 curl 数据获取器

        Args:
            proxy_port: 代理端口
        """
        self.proxy_port = proxy_port
        self.proxy_url = f"http://127.0.0.1:{proxy_port}"

    def fetch_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust='qfq'
    ) -> Optional[pd.DataFrame]:
        """
        使用 curl 获取股票K线数据

        Args:
            symbol: 股票代码
            start_date: 开始日期（格式：20240301）
            end_date: 结束日期（格式：20240308）
            adjust: 复权类型

        Returns:
            DataFrame or None
        """
        # 构造东方财富接口参数
        secid = f"0.{symbol}" if symbol.startswith('0') or symbol.startswith('3') else f"1.{symbol}"
        fqt = "1" if adjust == 'qfq' else ("2" if adjust == 'hfq' else "")

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
            'beg': start_date,
            'end': end_date,
            'rtntype': '6',
            'secid': secid,
            'klt': '101',
            'fqt': fqt
        }

        # 构造 curl 命令
        curl_cmd = [
            'curl', '-s',
            '-x', self.proxy_url,
            '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            url
        ]

        # 添加参数
        for key, value in params.items():
            curl_cmd.extend(['-G', '-d', f'{key}={value}'])

        try:
            # 执行 curl 命令
            result = subprocess.run(
                curl_cmd,
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode != 0:
                logger.error(f"curl 失败: {result.stderr}")
                return None

            # 解析 JSON
            data = json.loads(result.stdout)

            if not data or not data.get('data'):
                logger.warning(f"{symbol} 返回空数据")
                return None

            items = data['data']
            klines = items.get('klines', [])

            if not klines:
                logger.warning(f"{symbol} 没有K线数据")
                return None

            # 解析K线数据
            df = self._parse_klines(klines, symbol)
            logger.info(f"✅ {symbol} 成功获取 {len(df)} 行数据")

            return df

        except subprocess.TimeoutExpired:
            logger.error(f"curl 超时")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"获取失败: {e}")
            return None

    def _parse_klines(self, klines: list, symbol: str) -> pd.DataFrame:
        """
        解析K线数据

        K线格式: "日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率"
        """
        data = []

        for kline in klines:
            parts = kline.split(',')
            if len(parts) < 12:
                continue

            try:
                data.append({
                    'symbol': symbol,
                    'trade_date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': float(parts[5]),
                    'amount': float(parts[6]),
                    'pct_chg': float(parts[8]),
                    'turnover_rate': float(parts[10])
                })
            except (ValueError, IndexError) as e:
                logger.debug(f"解析K线失败: {kline}, {e}")
                continue

        df = pd.DataFrame(data)
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        return df


# 测试
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    fetcher = CurlDataFetcher(proxy_port=7897)

    print("=" * 60)
    print("测试 curl 获取K线数据")
    print("=" * 60)

    df = fetcher.fetch_stock_kline(
        symbol='000001',
        start_date='20240301',
        end_date='20240308'
    )

    if df is not None and not df.empty:
        print(f"\n✅ 成功获取 {len(df)} 行数据")
        print(f"\n数据预览:")
        print(df.head())
        print(f"\n数据类型:")
        print(df.dtypes)
    else:
        print("\n❌ 获取失败")
