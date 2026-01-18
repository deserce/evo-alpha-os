"""
EvoAlpha OS - 新闻舆情数据采集
采集财经新闻并进行股票关联和情绪分析
"""

import sys
import os
import time
import logging
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta
import re

# ================= 网络急救包 =================
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if k in os.environ:
        del os.environ[k]

import ssl
ssl._create_default_https_context = ssl._create_unverified_context
# ==========================================================

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.database import get_active_engines

# ================= 日志配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class NewsManager:
    def __init__(self):
        self.engines = get_active_engines()
        self.articles_table = "news_articles"
        self.relation_table = "news_stock_relation"

    def _init_tables(self):
        """初始化新闻相关表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建新闻表...")
            try:
                with engine.begin() as conn:
                    # 新闻文章表
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

                    # 新闻-股票关联表
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.relation_table} (
                            article_id VARCHAR(50),
                            symbol VARCHAR(20),
                            relevance_score FLOAT,
                            sentiment_type VARCHAR(10),
                            PRIMARY KEY (article_id, symbol)
                        );
                    """))

                    # 创建索引
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_news_time ON {self.articles_table} (publish_time);"))
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_news_symbol ON {self.relation_table} (symbol);"))

                    logger.success(f"✅ [{mode}] 新闻表创建成功")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建新闻表失败: {e}")

    def fetch_news_em(self, date_str=None):
        """
        从东方财富获取新闻

        Args:
            date_str: 日期字符串（YYYYMMDD），默认为今天

        Returns:
            DataFrame: 新闻数据
        """
        try:
            if date_str is None:
                date_str = datetime.now().strftime('%Y%m%d')

            # 获取东方财富新闻
            df = ak.stock_news_em(date=date_str)

            if df.empty:
                logger.warning(f"⚠️  {date_str} 无新闻数据")
                return None

            # 数据清洗
            df = df.rename(columns={
                '新闻标题': 'title',
                '新闻内容': 'content',
                '新闻来源': 'source',
                '发布时间': 'publish_time',
                '文章链接': 'url',
            })

            # 生成 article_id（使用URL的哈希值作为ID）
            df['article_id'] = df['url'].apply(lambda x: f"EM_{hash(x) % 10000000000:08d}")

            # 时间转换
            df['publish_time'] = pd.to_datetime(df['publish_time'])

            # 默认情绪类型（后续可以优化）
            df['sentiment_type'] = 'neutral'

            logger.info(f"  ✅ 东方财富: {len(df)} 条新闻")
            return df

        except Exception as e:
            logger.error(f"❌ 东方财富新闻采集失败: {e}")
            return None

    def extract_stock_symbols(self, text):
        """
        从文本中提取股票代码

        Args:
            text: 新闻文本

        Returns:
            list: 股票代码列表
        """
        # 匹配6位数字（可能是股票代码）
        pattern = r'\b(00|30|60|68)\d{4}\b'
        matches = re.findall(pattern, text)

        # 去重
        symbols = list(set(matches))
        return symbols

    def analyze_sentiment(self, title, content):
        """
        简单的情绪分析（基于关键词）

        Args:
            title: 新闻标题
            content: 新闻内容

        Returns:
            str: 'positive', 'negative', 'neutral'
        """
        # 利好关键词
        positive_keywords = ['大涨', '上涨', '利好', '突破', '增长', '盈利', '涨停',
                            '回购', '增持', '收购', '业绩', '创新高', '领涨']

        # 利空关键词
        negative_keywords = ['大跌', '下跌', '利空', '跌破', '下降', '亏损', '跌停',
                            '减持', '业绩', '创新低', '领跌', '调查', '处罚']

        # 合并文本
        text = title + ' ' + str(content)

        # 统计关键词
        positive_count = sum(1 for kw in positive_keywords if kw in text)
        negative_count = sum(1 for kw in negative_keywords if kw in text)

        # 判断情绪
        if positive_count > negative_count:
            return 'positive'
        elif negative_count > positive_count:
            return 'negative'
        else:
            return 'neutral'

    def save_news(self, df):
        """
        保存新闻到数据库

        Args:
            df: 新闻数据
        """
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 保存文章
                    articles_df = df[['article_id', 'title', 'content', 'source', 'publish_time', 'url', 'sentiment_type']]

                    # 删除已存在的文章
                    conn.execute(text(f"""
                        DELETE FROM {self.articles_table}
                        WHERE article_id IN ({','.join([f"'{aid}'" for aid in articles_df['article_id']])})
                    """))

                    # 插入新文章
                    articles_df.to_sql(self.articles_table, conn, if_exists='append', index=False)

                    # 保存股票关联
                    relations = []
                    for _, row in df.iterrows():
                        symbols = self.extract_stock_symbols(row['title'] + ' ' + str(row['content']))

                        for symbol in symbols:
                            relations.append({
                                'article_id': row['article_id'],
                                'symbol': symbol,
                                'relevance_score': 1.0,  # 默认相关性
                                'sentiment_type': row['sentiment_type']
                            })

                    if relations:
                        relations_df = pd.DataFrame(relations)

                        # 删除旧关联
                        conn.execute(text(f"""
                            DELETE FROM {self.relation_table}
                            WHERE article_id IN ({','.join([f"'{aid}'" for aid in articles_df['article_id']])})
                        """))

                        # 插入新关联
                        relations_df.to_sql(self.relation_table, conn, if_exists='append', index=False)

                    logger.info(f"✅ [{mode}] 保存 {len(df)} 条新闻，{len(relations)} 个股票关联")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存新闻失败: {e}")

    def run(self, days=3):
        """
        执行新闻采集

        Args:
            days: 采集最近几天的新闻
        """
        logger.info("🚀 开始采集新闻舆情数据...")

        # 初始化表
        self._init_tables()

        # 采集最近几天的新闻
        for i in range(days):
            date = datetime.now() - timedelta(days=i)
            date_str = date.strftime('%Y%m%d')

            logger.info(f"📰 采集 {date_str} 的新闻...")

            try:
                # 获取新闻
                df = self.fetch_news_em(date_str)

                if df is not None:
                    # 分析情绪
                    df['sentiment_type'] = df.apply(
                        lambda row: self.analyze_sentiment(row['title'], row['content']),
                        axis=1
                    )

                    # 保存到数据库
                    self.save_news(df)

                # 避免请求过快
                time.sleep(1)

            except Exception as e:
                logger.error(f"❌ {date_str} 新闻采集失败: {e}")
                continue

        logger.success("🎉 新闻舆情采集完成")


if __name__ == "__main__":
    manager = NewsManager()
    manager.run(days=3)  # 默认采集最近3天
