"""EvoAlpha OS - 新闻舆情数据采集器。"""

import argparse
import time
import hashlib
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta, date
import re

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

from app.core.database import get_active_engines

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class NewsCollector(BaseCollector):
    """新闻舆情数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="news",
            request_timeout=30,
            request_delay=1,
            max_retries=3
        )
        self.engines = get_active_engines()
        self.articles_table = "news_articles"
        self.relation_table = "news_stock_relation"

    @staticmethod
    def _parse_date(value):
        if value is None:
            return None
        if isinstance(value, date):
            return value
        return pd.to_datetime(value, errors="raise").date()

    @staticmethod
    def _stable_article_id(url: str) -> str:
        digest = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16]
        return f"NEWS_{digest}"

    def _init_tables(self):
        """初始化新闻相关表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建新闻表...")
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.articles_table} (
                            article_id VARCHAR(50) PRIMARY KEY,
                            title VARCHAR(200),
                            content TEXT,
                            source VARCHAR(50),
                            publish_time TIMESTAMP,
                            url VARCHAR(500),
                            sentiment_type VARCHAR(10),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                    """))

                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.relation_table} (
                            article_id VARCHAR(50),
                            symbol VARCHAR(20),
                            relevance_score FLOAT,
                            sentiment_type VARCHAR(10),
                            PRIMARY KEY (article_id, symbol)
                        );
                    """))

                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_news_time ON {self.articles_table} (publish_time);"))
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_news_symbol ON {self.relation_table} (symbol);"))

                    logger.info(f"✅ [{mode}] 新闻表创建成功")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建新闻表失败: {e}")

    def fetch_news_em(self, date_str=None):
        """从可用资讯源获取市场新闻。

        stock_news_em 默认只返回单只股票的新闻，这里改用财新数据通最新资讯流，
        避免把 603777 的个股新闻误当成全市场新闻。
        """
        try:
            df = self._retry_call(ak.stock_news_main_cx)

            if df.empty:
                logger.warning(f"⚠️  无新闻数据")
                return None

            df = df.rename(columns={
                'tag': 'title',
                'summary': 'content',
            })
            df['source'] = '财新数据通'
            publish_date = df['url'].astype(str).str.extract(r"/(\d{4}-\d{2}-\d{2})/", expand=False)
            df['publish_time'] = pd.to_datetime(publish_date, errors='coerce')
            df = df.dropna(subset=['publish_time', 'url'])
            if date_str:
                target_date = pd.to_datetime(date_str, format="%Y%m%d", errors="coerce").date()
                df = df[df['publish_time'].dt.date == target_date]

            if df.empty:
                logger.warning(f"⚠️  {date_str or 'latest'} 无新闻数据")
                return None

            df['article_id'] = df['url'].apply(self._stable_article_id)
            df['sentiment_type'] = 'neutral'

            logger.info(f"  ✅ 财新数据通: {len(df)} 条新闻")
            return df

        except Exception as e:
            logger.error(f"❌ 新闻采集失败: {e}")
            return None

    def extract_stock_symbols(self, text):
        """从文本中提取股票代码"""
        pattern = r'\b(?:00|30|60|68)\d{4}\b'
        matches = re.findall(pattern, text)
        symbols = list(set(matches))
        return symbols

    def analyze_sentiment(self, title, content):
        """简单的情绪分析（基于关键词）"""
        positive_keywords = ['大涨', '上涨', '利好', '突破', '增长', '盈利', '涨停',
                            '回购', '增持', '收购', '业绩', '创新高', '领涨']

        negative_keywords = ['大跌', '下跌', '利空', '跌破', '下降', '亏损', '跌停',
                            '减持', '业绩', '创新低', '领跌', '调查', '处罚']

        text = title + ' ' + str(content)

        positive_count = sum(1 for kw in positive_keywords if kw in text)
        negative_count = sum(1 for kw in negative_keywords if kw in text)

        if positive_count > negative_count:
            return 'positive'
        elif negative_count > positive_count:
            return 'negative'
        else:
            return 'neutral'

    def save_news(self, df):
        """保存新闻到数据库"""
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    articles_df = df[['article_id', 'title', 'content', 'source', 'publish_time', 'url', 'sentiment_type']]

                    article_ids = [str(aid) for aid in articles_df['article_id']]
                    for article_id in article_ids:
                        conn.execute(text(f"""
                            DELETE FROM {self.articles_table}
                            WHERE article_id = :article_id
                        """), {"article_id": article_id})

                    articles_df.to_sql(self.articles_table, conn, if_exists='append', index=False)

                    relations = []
                    for _, row in df.iterrows():
                        symbols = self.extract_stock_symbols(row['title'] + ' ' + str(row['content']))

                        for symbol in symbols:
                            relations.append({
                                'article_id': row['article_id'],
                                'symbol': symbol,
                                'relevance_score': 1.0,
                                'sentiment_type': row['sentiment_type']
                            })

                    if relations:
                        relations_df = pd.DataFrame(relations)

                        for article_id in article_ids:
                            conn.execute(text(f"""
                                DELETE FROM {self.relation_table}
                                WHERE article_id = :article_id
                            """), {"article_id": article_id})

                        relations_df.to_sql(self.relation_table, conn, if_exists='append', index=False)

                    logger.info(f"✅ [{mode}] 保存 {len(df)} 条新闻，{len(relations)} 个股票关联")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存新闻失败: {e}")

    def get_last_date(self):
        """获取最后采集的新闻日期"""
        for mode, engine in self.engines:
            try:
                with engine.connect() as conn:
                    # 转换为date以便比较
                    query = text(f"SELECT DATE(MAX(publish_time)) as last_date FROM {self.articles_table}")
                    result = conn.execute(query).scalar()
                    if result:
                        # 确保返回 date 对象
                        if isinstance(result, str):
                            from datetime import datetime
                            result = datetime.strptime(result, '%Y-%m-%d').date()
                        elif isinstance(result, datetime):
                            result = result.date()
                        logger.info(f"✅ [{mode}] 最后采集日期: {result}")
                        return result
            except Exception as e:
                logger.warning(f"⚠️  [{mode}] 获取最后日期失败: {e}")
                continue

        return None

    def run(self, days=None, start=None, end=None):
        """
        执行新闻采集（增量更新）

        Args:
            days: 采集最近几天的新闻（仅用于首次采集或手动指定）
                   None 表示增量更新（只采集缺失的日期）
            start: 显式起始日期（YYYY-MM-DD），优先于 days
            end: 显式结束日期（YYYY-MM-DD），优先于今天
        """
        self.log_collection_start()
        logger.info("🚀 开始采集新闻舆情数据...")

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_tables()

        # 确定采集日期范围
        start_target = self._parse_date(start)
        end_target = self._parse_date(end)
        if start_target or end_target:
            if start_target is None:
                start_target = end_target
            if end_target is None:
                end_target = start_target
            start_date = start_target
            today = end_target
            logger.info(f"📅 目标日期模式：采集 {start_date} 至 {today} 新闻")
        elif days is not None:
            # 手动指定天数
            start_date = date.today() - timedelta(days=days-1)
            today = date.today()
            logger.info(f"📅 手动模式：采集最近 {days} 天新闻")
        else:
            # 增量更新模式：获取最后采集日期
            last_date = self.get_last_date()
            if last_date:
                # 从最后日期+1天开始采集
                start_date = last_date + timedelta(days=1)
                logger.info(f"📅 增量模式：从 {start_date} 至今")
            else:
                # 首次采集，采集最近3天新闻
                start_date = date.today() - timedelta(days=2)
                logger.info(f"🆕 首次采集：采集最近3天新闻")

            today = date.today()

        # 检查是否需要更新
        if start_date > today:
            logger.info(f"✅ 新闻数据已是最新，无需更新")
            self.log_collection_end(True, "数据已是最新")
            return

        # 计算需要采集的天数
        days_to_collect = (today - start_date).days + 1
        logger.info(f"📊 需要采集 {days_to_collect} 天新闻")

        total_articles = 0
        success_count = 0
        for i in range(days_to_collect):
            current_date = start_date + timedelta(days=i)
            date_str = current_date.strftime('%Y%m%d')

            logger.info(f"📰 [{i+1}/{days_to_collect}] 采集 {date_str} 的新闻...")

            try:
                df = self.fetch_news_em(date_str)

                if df is not None and not df.empty:
                    # 情绪分析
                    df['sentiment_type'] = df.apply(
                        lambda row: self.analyze_sentiment(row['title'], row['content']),
                        axis=1
                    )

                    # 保存新闻
                    self.save_news(df)
                    total_articles += len(df)
                    success_count += 1
                    logger.info(f"  ✅ 采集到 {len(df)} 条新闻")

                time.sleep(self.request_delay)

            except Exception as e:
                logger.error(f"❌ {date_str} 新闻采集失败: {e}")
                continue

        logger.info(f"🎉 新闻舆情采集完成，成功 {success_count}/{days_to_collect} 天，共 {total_articles} 条新闻")
        self.log_collection_end(True, f"成功 {success_count}/{days_to_collect} 天，共 {total_articles} 条新闻")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect market news")
    parser.add_argument("--start", help="start date, e.g. 2026-06-04")
    parser.add_argument("--end", help="end date, e.g. 2026-06-04")
    parser.add_argument("--days", type=int, default=3, help="fallback recent-day window")
    args = parser.parse_args()
    collector = NewsCollector()
    collector.run(days=args.days, start=args.start, end=args.end)
