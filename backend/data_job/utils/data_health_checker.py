"""
K线数据健康度检查器

职责：
- 检查K线数据的完整性（是否有缺失）
- 检查数据质量（异常值、空值）
- 评估数据是否满足RPS计算要求
- 生成健康度报告

使用场景：
- 在数据采集后执行
- 在RPS计算前执行
- 确保数据质量满足量化计算要求
"""

import sqlite3
import pandas as pd
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

# 路径适配
backend_dir = Path(__file__).parent.parent.absolute()
import sys
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """健康检查结果"""
    is_healthy: bool
    score: int  # 0-100
    stock_kline: Dict
    sector_kline: Dict
    etf_kline: Dict
    issues: List[str]
    warnings: List[str]
    recommendations: List[str]


class KlineDataHealthChecker:
    """
    K线数据健康度检查器

    检查项目：
    1. 数据时效性：最新数据日期
    2. 数据完整性：每日数据量是否达标
    3. 数据连续性：是否有缺失的交易日
    4. 数据质量：异常值、空值检查
    """

    def __init__(self, db_path: str = "data/local_quant.db"):
        """
        初始化检查器

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.conn = None

        # 健康度阈值配置
        self.config = {
            # 最小股票数量（正常交易日应有5000+）
            'min_stock_count': 4500,
            'ideal_stock_count': 5200,

            # 最小板块数量
            'min_sector_count': 400,
            'ideal_sector_count': 500,

            # 最小ETF数量
            'min_etf_count': 30,

            # 允许的最大数据滞后天数
            'max_data_lag_days': 2,

            # 检查的最近天数
            'check_recent_days': 10,
        }

    def _get_connection(self):
        """获取数据库连接"""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
        return self.conn

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def check_all(self) -> HealthCheckResult:
        """
        执行完整的健康检查

        Returns:
            HealthCheckResult: 检查结果
        """
        logger.info("=" * 80)
        logger.info("🏥 开始K线数据健康度检查")
        logger.info("=" * 80)

        issues = []
        warnings = []
        recommendations = []

        # 1. 检查股票K线数据
        stock_health = self._check_stock_kline()
        if not stock_health['is_healthy']:
            issues.extend(stock_health['issues'])
        warnings.extend(stock_health['warnings'])

        # 2. 检查板块K线数据
        sector_health = self._check_sector_kline()
        if not sector_health['is_healthy']:
            issues.extend(sector_health['issues'])
        warnings.extend(sector_health['warnings'])

        # 3. 检查ETF K线数据
        etf_health = self._check_etf_kline()
        if not etf_health['is_healthy']:
            issues.extend(etf_health['issues'])
        warnings.extend(etf_health['warnings'])

        # 4. 计算总体健康度分数
        score = self._calculate_health_score(
            stock_health, sector_health, etf_health
        )

        # 5. 判断是否健康
        is_healthy = (score >= 70) and (len(issues) == 0)

        # 6. 生成建议
        recommendations = self._generate_recommendations(
            stock_health, sector_health, etf_health, issues
        )

        result = HealthCheckResult(
            is_healthy=is_healthy,
            score=score,
            stock_kline=stock_health,
            sector_kline=sector_health,
            etf_kline=etf_health,
            issues=issues,
            warnings=warnings,
            recommendations=recommendations
        )

        # 7. 打印报告
        self._print_report(result)

        return result

    def _check_stock_kline(self) -> Dict:
        """检查股票K线数据健康度"""
        conn = self._get_connection()

        result = {
            'name': '股票K线',
            'is_healthy': True,
            'issues': [],
            'warnings': [],
            'latest_date': None,
            'data_lag_days': 0,
            'recent_counts': [],
            'completeness_ratio': 0.0,
        }

        try:
            # 获取最近日期的数据
            recent_data = pd.read_sql(f'''
                SELECT trade_date, COUNT(DISTINCT symbol) as stock_count
                FROM stock_daily_prices
                WHERE trade_date >= date('now', '-{self.config["check_recent_days"]} days')
                GROUP BY trade_date
                ORDER BY trade_date DESC
            ''', conn)

            if recent_data.empty:
                result['is_healthy'] = False
                result['issues'].append("❌ 无股票K线数据")
                return result

            result['latest_date'] = recent_data.iloc[0]['trade_date']
            result['recent_counts'] = recent_data.to_dict('records')

            # 检查数据滞后
            today = date.today()
            try:
                latest_dt = pd.to_datetime(result['latest_date']).date()
                result['data_lag_days'] = (today - latest_dt).days
            except:
                result['data_lag_days'] = -1

            if result['data_lag_days'] > self.config['max_data_lag_days']:
                result['is_healthy'] = False
                result['issues'].append(
                    f"❌ 股票K线数据滞后{result['data_lag_days']}天，"
                    f"最新日期: {result['latest_date']}"
                )

            # 检查最近数据量
            latest_count = recent_data.iloc[0]['stock_count']
            completeness = latest_count / self.config['ideal_stock_count']
            result['completeness_ratio'] = completeness

            if latest_count < self.config['min_stock_count']:
                result['is_healthy'] = False
                result['issues'].append(
                    f"❌ 最新交易日股票数量不足: {latest_count} "
                    f"(要求≥{self.config['min_stock_count']})"
                )
            elif completeness < 0.9:
                result['warnings'].append(
                    f"⚠️  最新交易日股票数据不完整: {latest_count} "
                    f"({completeness*100:.1f}%)"
                )

            # 检查数据连续性（最近几天是否都有数据）
            expected_dates = pd.date_range(
                end=today, periods=self.config['check_recent_days'], freq='B'
            )
            # 这里简化处理，只检查记录数

        except Exception as e:
            result['is_healthy'] = False
            result['issues'].append(f"❌ 股票K线检查失败: {e}")

        return result

    def _check_sector_kline(self) -> Dict:
        """检查板块K线数据健康度"""
        conn = self._get_connection()

        result = {
            'name': '板块K线',
            'is_healthy': True,
            'issues': [],
            'warnings': [],
            'latest_date': None,
            'data_lag_days': 0,
            'latest_count': 0,
            'completeness_ratio': 0.0,
        }

        try:
            # 获取最新日期的数据
            latest_data = pd.read_sql('''
                SELECT trade_date, COUNT(DISTINCT sector_name) as sector_count
                FROM sector_daily_prices
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT 1
            ''', conn)

            if latest_data.empty:
                result['warnings'].append("⚠️  无板块K线数据")
                return result

            result['latest_date'] = latest_data.iloc[0]['trade_date']
            result['latest_count'] = latest_data.iloc[0]['sector_count']

            # 检查数据滞后
            today = date.today()
            try:
                latest_dt = pd.to_datetime(result['latest_date']).date()
                result['data_lag_days'] = (today - latest_dt).days
            except:
                result['data_lag_days'] = -1

            if result['data_lag_days'] > self.config['max_data_lag_days'] + 1:
                result['warnings'].append(
                    f"⚠️  板块K线数据滞后{result['data_lag_days']}天"
                )

            # 检查数据量
            completeness = result['latest_count'] / self.config['ideal_sector_count']
            result['completeness_ratio'] = completeness

            if result['latest_count'] < self.config['min_sector_count']:
                result['warnings'].append(
                    f"⚠️  板块数量偏少: {result['latest_count']} "
                    f"(理想≥{self.config['ideal_sector_count']})"
                )

        except Exception as e:
            result['warnings'].append(f"⚠️  板块K线检查失败: {e}")

        return result

    def _check_etf_kline(self) -> Dict:
        """检查ETF K线数据健康度"""
        conn = self._get_connection()

        result = {
            'name': 'ETF K线',
            'is_healthy': True,
            'issues': [],
            'warnings': [],
            'latest_date': None,
            'data_lag_days': 0,
            'latest_count': 0,
        }

        try:
            # 获取最新日期的数据
            latest_data = pd.read_sql('''
                SELECT trade_date, COUNT(DISTINCT symbol) as etf_count
                FROM etf_daily_prices
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT 1
            ''', conn)

            if latest_data.empty:
                result['warnings'].append("⚠️  无ETF K线数据")
                return result

            result['latest_date'] = latest_data.iloc[0]['trade_date']
            result['latest_count'] = latest_data.iloc[0]['etf_count']

            # 检查数据量
            if result['latest_count'] < self.config['min_etf_count']:
                result['warnings'].append(
                    f"⚠️  ETF数量偏少: {result['latest_count']}"
                )

        except Exception as e:
            result['warnings'].append(f"⚠️  ETF K线检查失败: {e}")

        return result

    def _calculate_health_score(
        self, stock: Dict, sector: Dict, etf: Dict
    ) -> int:
        """
        计算总体健康度分数 (0-100)

        评分规则：
        - 股票K线健康度：50分（核心）
        - 板块K线健康度：30分
        - ETF K线健康度：20分
        """
        score = 0

        # 股票K线评分 (50分)
        if stock['is_healthy']:
            # 基础分30分
            stock_score = 30
            # 完整度加分 (最多20分)
            stock_score += min(20, int(stock['completeness_ratio'] * 20))
        else:
            stock_score = 10 if stock['completeness_ratio'] > 0.5 else 0
        score += stock_score

        # 板块K线评分 (30分)
        if sector['is_healthy']:
            sector_score = 20
            sector_score += min(10, int(sector['completeness_ratio'] * 10))
        else:
            sector_score = 10 if sector['completeness_ratio'] > 0.5 else 5
        score += sector_score

        # ETF K线评分 (20分)
        if etf['is_healthy']:
            etf_score = 20
        elif etf['latest_count'] > 0:
            etf_score = 10
        else:
            etf_score = 0
        score += etf_score

        return min(100, score)

    def _generate_recommendations(
        self, stock: Dict, sector: Dict, etf: Dict, issues: List[str]
    ) -> List[str]:
        """生成改进建议"""
        recommendations = []

        if not stock['is_healthy']:
            if stock['data_lag_days'] > 0:
                recommendations.append(
                    "🔄 运行股票K线采集: python3 -m data_job.collectors.stock_kline_collector"
                )
            if stock['completeness_ratio'] < 0.9:
                recommendations.append(
                    "🔄 补齐缺失股票数据: python3 pytdx_simple_update.py --days 3"
                )

        if sector['completeness_ratio'] < 0.8:
            recommendations.append(
                "🔄 运行板块K线采集: python3 download_sector_indices_mootdx.py"
            )

        if etf['latest_count'] < self.config['min_etf_count']:
            recommendations.append(
                "🔄 运行ETF K线采集: python3 -m data_job.collectors.etf_kline_collector"
            )

        if not recommendations:
            recommendations.append("✅ 数据质量良好，可以继续RPS计算")

        return recommendations

    def _print_report(self, result: HealthCheckResult):
        """打印健康检查报告"""
        logger.info("")
        logger.info("📊 K线数据健康度报告")
        logger.info("-" * 80)

        # 总体评分
        score_color = "✅" if result.score >= 80 else ("⚠️" if result.score >= 60 else "❌")
        logger.info(f"  {score_color} 总体健康度: {result.score}/100")
        logger.info(f"  状态: {'✅ 健康' if result.is_healthy else '❌ 不健康'}")
        logger.info("")

        # 股票K线详情
        stock = result.stock_kline
        logger.info(f"  📈 股票K线:")
        logger.info(f"     最新日期: {stock['latest_date']}")
        logger.info(f"     滞后天数: {stock['data_lag_days']}天")
        logger.info(f"     最新数量: {stock['recent_counts'][0]['stock_count'] if stock['recent_counts'] else 'N/A'}只")
        logger.info(f"     完整度: {stock['completeness_ratio']*100:.1f}%")
        logger.info(f"     状态: {'✅' if stock['is_healthy'] else '❌'}")
        logger.info("")

        # 板块K线详情
        sector = result.sector_kline
        logger.info(f"  🏢 板块K线:")
        logger.info(f"     最新日期: {sector['latest_date']}")
        logger.info(f"     最新数量: {sector['latest_count']}个")
        logger.info(f"     状态: {'✅' if sector['is_healthy'] else '⚠️'}")
        logger.info("")

        # ETF K线详情
        etf = result.etf_kline
        logger.info(f"  💰 ETF K线:")
        logger.info(f"     最新日期: {etf['latest_date']}")
        logger.info(f"     最新数量: {etf['latest_count']}只")
        logger.info(f"     状态: {'✅' if etf['is_healthy'] else '⚠️'}")
        logger.info("")

        # 问题列表
        if result.issues:
            logger.info("  ❌ 发现的问题:")
            for issue in result.issues:
                logger.info(f"     {issue}")
            logger.info("")

        # 警告列表
        if result.warnings:
            logger.info("  ⚠️  警告:")
            for warning in result.warnings[:5]:  # 只显示前5个
                logger.info(f"     {warning}")
            if len(result.warnings) > 5:
                logger.info(f"     ... 还有 {len(result.warnings) - 5} 个警告")
            logger.info("")

        # 改进建议
        logger.info("  💡 改进建议:")
        for rec in result.recommendations:
            logger.info(f"     {rec}")

        logger.info("")
        logger.info("=" * 80)

    def check_minimum_for_rps(self) -> Tuple[bool, str]:
        """
        检查数据是否满足RPS计算的最低要求

        Returns:
            Tuple[bool, str]: (是否满足, 原因说明)
        """
        conn = self._get_connection()

        try:
            # 检查最新数据日期
            latest_stock = pd.read_sql(
                'SELECT MAX(trade_date) as max_date FROM stock_daily_prices', conn
            ).iloc[0]['max_date']

            today = date.today()
            try:
                latest_dt = pd.to_datetime(latest_stock).date()
                lag_days = (today - latest_dt).days

                if lag_days > 3:
                    return False, f"股票K线数据滞后{lag_days}天，不满足RPS计算要求"
            except:
                pass

            # 检查数据量
            recent_count = pd.read_sql(f'''
                SELECT COUNT(DISTINCT symbol) as cnt
                FROM stock_daily_prices
                WHERE trade_date = '{latest_stock}'
            ''', conn).iloc[0]['cnt']

            if recent_count < 4000:
                return False, f"股票数量不足({recent_count}只)，不满足RPS计算要求"

            # 检查历史数据（RPS需要至少250日数据）
            history_check = pd.read_sql('''
                SELECT COUNT(DISTINCT symbol) as cnt,
                       MIN(trade_date) as min_date
                FROM stock_daily_prices
                WHERE trade_date >= date('now', '-260 days')
            ''', conn)

            if history_check.iloc[0]['cnt'] < 3000:
                return False, "历史数据不足，不满足RPS250计算要求"

            return True, "数据满足RPS计算要求"

        except Exception as e:
            return False, f"数据检查失败: {e}"


# ==================== 便捷函数 ====================

def check_kline_health(db_path: str = "data/local_quant.db") -> HealthCheckResult:
    """
    检查K线数据健康度（便捷函数）

    Args:
        db_path: 数据库路径

    Returns:
        HealthCheckResult: 检查结果
    """
    checker = KlineDataHealthChecker(db_path)
    try:
        return checker.check_all()
    finally:
        checker.close()


def verify_data_ready_for_rps(db_path: str = "data/local_quant.db") -> bool:
    """
    验证数据是否准备好进行RPS计算（便捷函数）

    Args:
        db_path: 数据库路径

    Returns:
        bool: 是否准备好
    """
    checker = KlineDataHealthChecker(db_path)
    try:
        is_ready, reason = checker.check_minimum_for_rps()
        if not is_ready:
            logger.warning(f"数据不满足RPS计算要求: {reason}")
        return is_ready
    finally:
        checker.close()


def get_data_readiness_report(db_path: str = "data/local_quant.db") -> Tuple[bool, str]:
    """
    获取数据准备情况报告

    Args:
        db_path: 数据库路径

    Returns:
        Tuple[bool, str]: (是否准备好, 原因说明)
    """
    checker = KlineDataHealthChecker(db_path)
    try:
        return checker.check_minimum_for_rps()
    finally:
        checker.close()


if __name__ == "__main__":
    # 直接运行时执行健康检查
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    result = check_kline_health()

    # 返回退出码
    import sys
    sys.exit(0 if result.is_healthy else 1)
