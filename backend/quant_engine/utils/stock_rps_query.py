"""
个股RPS查询工具

功能：
- 查询个股的RPS数据（5/10/20/50/120/250日）
- 查询个股所属行业的RPS数据
- 查询个股相关的概念板块RPS数据
- 对比个股与板块的相对强度
"""

import sys
from pathlib import Path
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import sqlite3
import pandas as pd
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class StockRPSQuery:
    """个股RPS查询器"""

    def __init__(self, db_path: str = "data/local_quant.db"):
        self.db_path = db_path
        self.conn = None

    def _get_connection(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
        return self.conn

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _sector_rps_table(self) -> str:
        """Return the canonical sector RPS table when it is installed.

        The table name comes only from SQLite metadata, never from a caller,
        so the interpolated identifier remains safe while old databases can
        still be inspected during the additive migration.
        """
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('quant_feature_sector_rps_v2', 'quant_feature_sector_rps') "
            "ORDER BY CASE name WHEN 'quant_feature_sector_rps_v2' THEN 0 ELSE 1 END"
        ).fetchall()
        return str(rows[0][0]) if rows else "quant_feature_sector_rps_v2"

    def query_stock_rps(
        self,
        symbol: str,
        trade_date: Optional[str] = None
    ) -> Dict:
        """
        查询个股RPS数据

        Args:
            symbol: 股票代码
            trade_date: 查询日期，默认为最新日期

        Returns:
            Dict: RPS数据
        """
        conn = self._get_connection()

        # 获取最新日期
        if trade_date is None:
            latest = pd.read_sql(
                'SELECT MAX(trade_date) as max_date FROM stock_daily_prices WHERE symbol = ?',
                conn, params=[symbol]
            ).iloc[0]['max_date']
            if latest:
                trade_date = pd.to_datetime(latest).strftime('%Y-%m-%d')

        if not trade_date:
            return {"error": "无数据"}

        # 查询RPS数据
        query = """
            SELECT symbol, trade_date,
                   rps_5, rps_10, rps_20, rps_50, rps_120, rps_250,
                   chg_5, chg_10, chg_20, chg_50, chg_120, chg_250
            FROM quant_feature_rps
            WHERE symbol = ? AND trade_date LIKE ?
        """

        try:
            df = pd.read_sql(query, conn, params=[symbol, f'{trade_date}%'])

            if df.empty:
                return {"error": f"无RPS数据 (日期: {trade_date})"}

            # 获取股票基本信息
            info = pd.read_sql(
                'SELECT name FROM stock_info WHERE symbol = ?', conn, params=[symbol]
            ).iloc[0]

            result = df.iloc[0].to_dict()
            result['stock_name'] = info['name']
            result['query_date'] = trade_date

            return result

        except Exception as e:
            return {"error": str(e)}

    def query_stock_with_sector_rps(
        self,
        symbol: str,
        trade_date: Optional[str] = None
    ) -> Dict:
        """
        查询个股及其所属行业的RPS数据

        Args:
            symbol: 股票代码
            trade_date: 查询日期

        Returns:
            Dict: 个股和行业RPS数据
        """
        conn = self._get_connection()

        # 获取最新日期
        if trade_date is None:
            latest = pd.read_sql(
                'SELECT MAX(trade_date) as max_date FROM stock_daily_prices', conn
            ).iloc[0]['max_date']
            if latest:
                trade_date = pd.to_datetime(latest).strftime('%Y-%m-%d')

        result = {
            'symbol': symbol,
            'trade_date': trade_date,
            'stock_rps': None,
            'industry_rps': None,
            'concept_sectors': []
        }

        # 1. 查询个股RPS
        stock_rps = pd.read_sql("""
            SELECT rps_20, rps_50, rps_120, rps_250
            FROM quant_feature_rps
            WHERE symbol = ? AND trade_date LIKE ?
        """, conn, params=[symbol, f'{trade_date}%'])

        if not stock_rps.empty:
            result['stock_rps'] = stock_rps.iloc[0].to_dict()

        # 2. 查询所属行业
        # stock_sector_map 存储的是行业ID（数字），需要关联
        industry_info = pd.read_sql("""
            SELECT sm.sector_name as industry_id
            FROM stock_sector_map sm
            WHERE sm.symbol = ?
        """, conn, params=[symbol])

        if not industry_info.empty:
            industry_id = industry_info.iloc[0]['industry_id']
            result['industry_id'] = industry_id

            # 行业RPS目前没有单独的表，这里返回行业ID
            # 可以通过计算行业内股票的平均RPS来获得

        # 3. 查询相关概念板块RPS（通过stock_sector_map中的行业ID去匹配概念板块）
        # 由于行业和概念是不同分类体系，这里简化处理
        # 实际中可以通过股票代码从sector_list中查找相关的概念板块

        # 获取股票名称
        stock_info = pd.read_sql(
            'SELECT name FROM stock_info WHERE symbol = ?', conn, params=[symbol]
        )
        if not stock_info.empty:
            result['stock_name'] = stock_info.iloc[0]['name']

        return result

    def query_stock_and_concepts_rps(
        self,
        symbol: str,
        trade_date: Optional[str] = None
    ) -> Dict:
        """
        查询个股及关联概念板块的RPS数据

        策略：
        1. 获取个股RPS
        2. 通过sector_daily_prices表查找包含该股票的板块
        3. 获取这些板块的RPS数据

        Args:
            symbol: 股票代码
            trade_date: 查询日期

        Returns:
            Dict: 个股和板块RPS数据
        """
        conn = self._get_connection()

        # 获取最新日期
        if trade_date is None:
            latest = pd.read_sql(
                'SELECT MAX(trade_date) as max_date FROM stock_daily_prices', conn
            ).iloc[0]['max_date']
            if latest:
                trade_date = pd.to_datetime(latest).strftime('%Y-%m-%d')

        result = {
            'symbol': symbol,
            'trade_date': trade_date,
            'stock_rps': None,
            'related_sectors': []
        }

        # 1. 查询个股RPS
        stock_rps = pd.read_sql("""
            SELECT rps_5, rps_10, rps_20, rps_50, rps_120, rps_250
            FROM quant_feature_rps
            WHERE symbol = ? AND trade_date LIKE ?
        """, conn, params=[symbol, f'{trade_date}%'])

        if stock_rps.empty:
            return {**result, "error": f"无RPS数据 (日期: {trade_date})"}

        result['stock_rps'] = stock_rps.iloc[0].to_dict()

        # 获取股票名称
        stock_info = pd.read_sql(
            'SELECT name FROM stock_info WHERE symbol = ?', conn, params=[symbol]
        )
        if not stock_info.empty:
            result['stock_name'] = stock_info.iloc[0]['name']

        # 2. 查找相关概念板块（从stock_sector_map获取）
        related_sectors = pd.read_sql("""
            SELECT DISTINCT sector_name
            FROM stock_sector_map
            WHERE symbol = ? AND sector_type = 'Concept'
        """, conn, params=[symbol])

        if not related_sectors.empty:
            sector_names = related_sectors['sector_name'].tolist()

            # 构建IN子句的占位符
            placeholders = ','.join(['?' for _ in sector_names])

            # 查询这些概念板块的RPS数据
            sector_rps_table = self._sector_rps_table()
            sector_rps = pd.read_sql(f"""
                SELECT sr.sector_name, sr.rps_5, sr.rps_10, sr.rps_20, sr.rps_50, sr.rps_120, sr.rps_250
                FROM {sector_rps_table} sr
                WHERE sr.trade_date LIKE ? AND sr.sector_name IN ({placeholders})
                ORDER BY sr.rps_20 DESC
            """, conn, params=[f'{trade_date}%'] + sector_names)

            if not sector_rps.empty:
                for _, row in sector_rps.iterrows():
                    result['related_sectors'].append({
                        'sector_name': row['sector_name'],
                        'rps_5': row['rps_5'],
                        'rps_10': row['rps_10'],
                        'rps_20': row['rps_20'],
                        'rps_50': row['rps_50'],
                        'rps_120': row['rps_120'],
                        'rps_250': row['rps_250'],
                    })

        return result

    def compare_stock_with_sectors(
        self,
        symbol: str,
        trade_date: Optional[str] = None,
        top_n: int = 10
    ) -> Dict:
        """
        对比个股与板块的RPS表现

        Args:
            symbol: 股票代码
            trade_date: 查询日期
            top_n: 展示RPS排名前N的板块

        Returns:
            Dict: 对比结果
        """
        conn = self._get_connection()

        # 获取最新日期
        if trade_date is None:
            latest = pd.read_sql(
                'SELECT MAX(trade_date) as max_date FROM stock_daily_prices', conn
            ).iloc[0]['max_date']
            if latest:
                trade_date = pd.to_datetime(latest).strftime('%Y-%m-%d')

        result = {
            'symbol': symbol,
            'trade_date': trade_date,
            'stock_rps': {},
            'top_sectors': []
        }

        # 查询个股RPS
        stock_rps = pd.read_sql("""
            SELECT rps_20, rps_50, rps_120, rps_250
            FROM quant_feature_rps
            WHERE symbol = ? AND trade_date LIKE ?
        """, conn, params=[symbol, f'{trade_date}%'])

        if stock_rps.empty:
            return {**result, "error": f"无RPS数据 (日期: {trade_date})"}

        result['stock_rps'] = stock_rps.iloc[0].to_dict()

        # 获取股票信息
        stock_info = pd.read_sql(
            'SELECT name FROM stock_info WHERE symbol = ?', conn, params=[symbol]
        )
        if not stock_info.empty:
            result['stock_name'] = stock_info.iloc[0]['name']

        # 获取板块RPS排名
        sector_rps_table = self._sector_rps_table()
        sector_rps = pd.read_sql(f"""
            SELECT s.sector_name, sr.rps_20, sr.rps_50, sr.rps_120, sr.rps_250
            FROM {sector_rps_table} sr
            JOIN sector_list s ON sr.sector_name = s.sector_name
            WHERE sr.trade_date LIKE ?
            ORDER BY sr.rps_20 DESC
            LIMIT ?
        """, conn, params=[f'{trade_date}%', top_n])

        if not sector_rps.empty:
            for _, row in sector_rps.iterrows():
                result['top_sectors'].append({
                    'sector_name': row['sector_name'],
                    'rps_20': row['rps_20'],
                    'rps_50': row['rps_50'],
                    'rps_120': row['rps_120'],
                    'rps_250': row['rps_250'],
                })

        return result


def print_stock_rps_report(symbol: str, trade_date: str = None):
    """
    打印个股RPS报告（命令行工具）

    Args:
        symbol: 股票代码
        trade_date: 查询日期 YYYY-MM-DD 格式
    """
    import sqlite3
    query = StockRPSQuery()

    try:
        result = query.query_stock_and_concepts_rps(symbol, trade_date)

        if 'error' in result:
            print(f"❌ {result['error']}")
            return

        print("=" * 80)
        print(f"📊 个股RPS查询报告")
        print("=" * 80)
        print(f"股票代码: {result.get('symbol', symbol)}")
        print(f"股票名称: {result.get('stock_name', 'N/A')}")
        print(f"查询日期: {result['trade_date']}")
        print()

        # 个股RPS
        stock_rps = result.get('stock_rps', {})
        if stock_rps:
            print("📈 个股RPS数据:")
            print(f"  RPS5:   {stock_rps.get('rps_5', 'N/A'):.2f}")
            print(f"  RPS10:  {stock_rps.get('rps_10', 'N/A'):.2f}")
            print(f"  RPS20:  {stock_rps.get('rps_20', 'N/A'):.2f}")
            print(f"  RPS50:  {stock_rps.get('rps_50', 'N/A'):.2f}")
            print(f"  RPS120: {stock_rps.get('rps_120', 'N/A'):.2f}")
            print(f"  RPS250: {stock_rps.get('rps_250', 'N/A'):.2f}")
            print()

        # 获取所有关联的概念板块（包括没有RPS数据的）
        conn = query._get_connection()
        all_concepts = pd.read_sql("""
            SELECT DISTINCT sector_name
            FROM stock_sector_map
            WHERE symbol = ? AND sector_type = 'Concept'
            ORDER BY sector_name
        """, conn, params=[symbol])

        # 将有RPS数据的板块存入字典
        rps_data = {s['sector_name']: s for s in result.get('related_sectors', [])}

        if not all_concepts.empty:
            print(f"🏢 关联概念板块 ({len(all_concepts)}个):")
            print(f"{'板块名称':<16s} {'RPS5':>7} {'RPS10':>7} {'RPS20':>7} {'RPS50':>7} {'RPS120':>7} {'RPS250':>7}")
            print("-" * 60)

            for concept_name in all_concepts['sector_name']:
                if concept_name in rps_data:
                    s = rps_data[concept_name]
                    print(f"{concept_name:<16s} {s['rps_5']:>7.2f} {s['rps_10']:>7.2f} {s['rps_20']:>7.2f} {s['rps_50']:>7.2f} {s['rps_120']:>7.2f} {s['rps_250']:>7.2f}")
                else:
                    print(f"{concept_name:<16s} {'无数据':>42}")

        print()
        print("=" * 80)

    finally:
        query.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="个股RPS查询工具")
    parser.add_argument('symbol', help='股票代码 (如: 600519)')
    parser.add_argument('--date', help='查询日期 YYYY-MM-DD (默认: 最新)')

    args = parser.parse_args()

    print_stock_rps_report(args.symbol, args.date)
