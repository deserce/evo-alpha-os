"""
EvoAlpha OS - 量化工作台 API

面向 MVP 前端提供日期切换、数据监控、买点、交易计划和快速回测视图。
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import fcntl
import os
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.v1.market_resolver import market_order_case
from app.api.v1.schemas import ApiResponse
from app.core.config import settings
from app.core.minute_chart import get_minute_chart
from data_core.quality import latest_quality_reports, latest_source_failures
from data_core.source_registry import (
    MODULE_STOCK_DAILY,
    api_data_sources,
    get_source,
    is_official_write_source,
)


router = APIRouter(prefix="/quant", tags=["量化工作台"])

DB_PATH = Path(settings.LOCAL_DB_PATH)
BACKEND_DIR = DB_PATH.parent.parent
LOG_DIR = BACKEND_DIR / "logs" / "manual_tasks"
WORKFLOW_LOCK_PATH = BACKEND_DIR / "data" / ".daily_data_workflow.lock"

MONITOR_TABLES = (
    ("stock_daily_prices", "trade_date", ("symbol", "trade_date"), True),
    ("stock_valuation_daily", "trade_date", ("code", "trade_date"), False),
    ("index_daily", "trade_date", ("index_code", "trade_date"), False),
    ("sector_daily_prices", "trade_date", ("sector_name", "trade_date"), True),
    ("sector_vendor_daily", "trade_date", ("sector_name", "trade_date"), False),
    ("sector_composite_daily", "trade_date", ("sector_name", "trade_date"), True),
    ("sector_membership_version", "effective_from", ("snapshot_id",), False),
    ("sector_membership_item", "snapshot_id", ("snapshot_id", "symbol"), False),
    ("etf_daily_prices", "trade_date", ("symbol", "trade_date"), False),
    ("limit_board_trading", "trade_date", ("symbol", "trade_date"), False),
    ("limit_down_trading", "trade_date", ("symbol", "trade_date"), False),
    ("limit_break_trading", "trade_date", ("symbol", "trade_date"), False),
    ("yesterday_limit_trading", "trade_date", ("symbol", "trade_date"), False),
    ("news_articles", "publish_time", ("url",), False),
    ("macro_indicators", "publish_date", ("indicator_code", "publish_date"), False),
    ("stock_finance_snapshot_tdx", "report_date", ("symbol", "report_date", "source"), False),
    ("stock_f10_sections", "fetched_at", ("symbol", "category", "source"), False),
    ("stock_financial_reports", "report_date", ("symbol", "report_type", "report_date", "source"), False),
    ("stock_research_reports", "publish_date", ("symbol", "report_id"), False),
    ("industry_research_reports", "publish_date", ("industry_code", "report_id"), False),
    ("stock_announcements_cninfo", "publish_date", ("symbol", "announcement_id"), False),
    ("stock_dragon_tiger_records", "trade_date", ("symbol", "trade_date", "reason"), False),
    ("stock_dragon_tiger_seats", "trade_date", ("symbol", "trade_date", "side", "seat_rank"), False),
    ("stock_lockup_expiry", "free_date", ("symbol", "free_date", "lockup_type"), False),
    ("stock_margin_trading", "trade_date", ("symbol", "trade_date"), False),
    ("stock_block_trades", "trade_date", ("symbol", "trade_date", "price", "buyer", "seller"), False),
    ("stock_holder_num", "report_date", ("symbol", "report_date"), False),
    ("stock_dividend_history", "ex_dividend_date", ("symbol", "ex_dividend_date", "plan"), False),
    ("stock_fund_flow_daily", "trade_date", ("symbol", "trade_date"), False),
    ("stock_concept_blocks", "fetched_at", ("symbol", "block_code"), False),
    ("market_hot_stocks", "rank_date", ("rank_date", "source", "rank", "symbol"), False),
    ("market_hot_concepts", "rank_date", ("rank_date", "source", "rank", "concept_name"), False),
    ("northbound_flow_daily", "trade_date", ("trade_date", "source"), False),
    ("quant_feature_rps", "trade_date", ("symbol", "trade_date"), True),
    ("quant_feature_sector_rps", "trade_date", ("sector_name", "trade_date"), True),
    ("quant_feature_sector_rps_v2", "trade_date", ("sector_name", "trade_date"), True),
    ("quant_feature_etf_rps", "trade_date", ("symbol", "trade_date"), False),
    ("market_strength_daily", "trade_date", ("index_code", "trade_date"), True),
    ("industry_strength_daily", "trade_date", ("sector_name", "trade_date"), True),
    ("structure_health_daily", "trade_date", ("symbol", "trade_date"), True),
    ("stock_lifecycle_state", "trade_date", ("symbol", "trade_date"), True),
    ("timing_signals_daily", "trade_date", ("symbol", "trade_date", "signal_type"), True),
    ("trade_plans", "trade_date", ("signal_id",), True),
    ("watch_daily_snapshot", "trade_date", ("symbol", "trade_date"), True),
    ("watch_events", "trade_date", ("dedupe_key",), True),
    ("notification_deliveries", "created_at", ("dedupe_key",), False),
    ("research_stock_daily_snapshot", "trade_date", ("theme_name", "symbol", "trade_date"), False),
)

DATE_COLUMN_CANDIDATES = (
    "trade_date",
    "date",
    "publish_time",
    "publish_date",
    "report_date",
    "hold_date",
    "add_date",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
)

RPS_PERIODS = (5, 10, 20, 50, 120, 250)
RPS_COLUMNS = {f"rps_{period}" for period in RPS_PERIODS}
SECTOR_RPS_COLUMNS = {f"sector_rps_{period}" for period in RPS_PERIODS}
COMMON_INDUSTRY_SECTORS = (
    "机械设备",
    "电子",
    "医药生物",
    "基础化工",
    "电力设备",
    "计算机",
    "汽车",
    "轻工制造",
    "建筑装饰",
    "国防军工",
    "有色金属",
    "公用事业",
    "环保",
    "传媒",
    "交通运输",
    "食品饮料",
    "通信",
    "纺织服饰",
    "农林牧渔",
    "商贸零售",
    "家用电器",
    "房地产",
    "非银金融",
    "社会服务",
    "建筑材料",
    "石油石化",
    "钢铁",
    "银行",
    "煤炭",
    "美容护理",
)

STAGE_GLOSSARY = [
    {"code": "M0", "name": "市场退潮", "scope": "市场温度", "description": "大盘弱势，默认防守，暂停新开仓。"},
    {"code": "M1", "name": "弱修复", "scope": "市场温度", "description": "风险仍高，只允许轻仓试错。"},
    {"code": "M2", "name": "震荡平衡", "scope": "市场温度", "description": "市场中性，控制仓位，等待行业确认。"},
    {"code": "M3", "name": "可交易", "scope": "市场温度", "description": "广度、均线和成交改善，可以按计划参与。"},
    {"code": "M4", "name": "强趋势", "scope": "市场温度", "description": "市场主升，优先顺势进攻。"},
    {"code": "I0", "name": "行业退潮", "scope": "行业战役", "description": "行业弱势或资金撤离，回避。"},
    {"code": "I1", "name": "行业弱修复", "scope": "行业战役", "description": "有局部反弹，但主线未成形。"},
    {"code": "I2", "name": "行业观察", "scope": "行业战役", "description": "强度中性，等待 RPS 和量能继续确认。"},
    {"code": "I3", "name": "行业转强", "scope": "行业战役", "description": "行业进入可交易区，寻找 S3/S4 梯队。"},
    {"code": "I4", "name": "主线行业", "scope": "行业战役", "description": "行业强度领先，优先寻找龙头与补涨。"},
    {"code": "S1", "name": "弱势/未入池", "scope": "个股生命周期", "description": "个股结构弱，暂不进入作战名单。"},
    {"code": "S2", "name": "底部修复", "scope": "个股生命周期", "description": "结构开始改善，仍以观察为主。"},
    {"code": "S3", "name": "右侧蓄势", "scope": "个股生命周期", "description": "趋势和 RPS 改善，等待触发点。"},
    {"code": "S4", "name": "结构突破", "scope": "个股生命周期", "description": "进入重点跟踪，允许按买点计划介入。"},
    {"code": "S5", "name": "强势信号", "scope": "个股生命周期", "description": "强势股进入作战状态，必须配合风控执行。"},
    {"code": "RPS", "name": "相对价格强度", "scope": "指标", "description": "同一交易日横向排名百分位，越接近 100 越强。"},
    {"code": "QFQ", "name": "前复权 K 线", "scope": "数据", "description": "把历史价格按复权因子调整，便于连续计算收益和 RPS。"},
    {"code": "P0/P1/P2", "name": "优先级", "scope": "工程", "description": "P0 是核心日更计算，P1 是买点/风控/研究，P2 是回测与增强。"},
]

SYSTEM_GUIDE = [
    {"title": "Dashboard 总览", "description": "当天市场温度、行业主线、S4/S5、买点、风控计划的第一屏。"},
    {"title": "Market 市场温度", "description": "回答今天能不能交易，核心看 M0-M4、市场广度、RPS90、成交和新高。"},
    {"title": "Industries 行业战役", "description": "回答主线在哪里，核心看 I0-I4、行业 RPS、行业强度、龙头和梯队。"},
    {"title": "Stocks 个股作战", "description": "回答个股能不能买，核心看生命周期、结构分、买点和交易计划。"},
    {"title": "Signals 买点信号", "description": "由 pocket pivot、突破、供给枯竭等触发，进入交易计划前必须过冷静期。"},
    {"title": "Portfolio & Review 复盘", "description": "复盘持仓风险、动态止损、减仓/退出动作与信号前瞻收益。"},
    {"title": "Data 管理台", "description": "负责采集、计算、补齐、任务日志、表健康和系统说明。"},
]

TASK_CATALOG = [
    {
        "id": "daily_kline_download",
        "title": "当天 K 线下载",
        "category": "采集",
        "schedule": "交易日 16:00 后",
        "description": "使用 TDX-only 正式源补充个股日 K。默认 mootdx + 本地前复权。",
        "danger": "medium",
    },
    {
        "id": "kline_backfill",
        "title": "K 线区间补齐",
        "category": "采集",
        "schedule": "按需手动",
        "description": "按开始/结束日期用 TDX-only 正式源补齐缺口；可限定单只股票或小样本测试。",
        "danger": "medium",
    },
    {
        "id": "rps_calculation",
        "title": "RPS 计算",
        "category": "计算",
        "schedule": "K 线完成后",
        "description": "计算个股、行业、ETF 的 RPS 因子。",
        "danger": "low",
    },
    {
        "id": "p0_calculation",
        "title": "P0 核心计算",
        "category": "计算",
        "schedule": "RPS 完成后",
        "description": "计算市场/行业强度、结构健康和生命周期。",
        "danger": "low",
    },
    {
        "id": "p1_calculation",
        "title": "P1 买点与风控",
        "category": "计算",
        "schedule": "P0 完成后",
        "description": "生成买点信号、交易计划和研究快照。",
        "danger": "low",
    },
    {
        "id": "mrgc_recalculation",
        "title": "指定日期 MRGC 重算",
        "category": "计算",
        "schedule": "按需手动",
        "description": "读取指定交易日的 K 线和 RPS，幂等重建 MRGC 预选结果。",
        "danger": "low",
    },
    {
        "id": "kline_then_rps",
        "title": "K 线 + RPS 快速链路",
        "category": "组合",
        "schedule": "按需手动",
        "description": "走生产入口，只跑 K 线、估值、RPS 和核心池，跳过 P0/P1。",
        "danger": "medium",
    },
    {
        "id": "daily_workflow",
        "title": "完整生产流水线",
        "category": "组合",
        "schedule": "交易日 16:00",
        "description": "统一生产入口 run_daily_data_workflow.py，从采集到 P1 全链路执行。",
        "danger": "high",
    },
    {
        "id": "workflow_check",
        "title": "工作流体检",
        "category": "检查",
        "schedule": "每次发布后",
        "description": "只打印任务计划、表快照和重复键，不执行采集计算。",
        "danger": "low",
    },
]

DATA_SOURCES = api_data_sources(include_diagnostic=True)

SCHEDULE_BINDINGS = [
    {"id": "daily_data_workflow", "name": "每日生产数据流水线", "cron": "周一至周五 16:00", "entrypoint": "backend/scripts/run_daily_data_workflow.py", "status": "bound"},
    {"id": "rps_after_kline", "name": "K线完成后 RPS", "cron": "随生产流水线串行执行", "entrypoint": "quant_engine.runner.feature_runner --calculators stock sector_composite etf", "status": "bound"},
    {"id": "data_health_check", "name": "数据健康体检", "cron": "按需手动/发布后", "entrypoint": "run_daily_data_workflow.py --check-only", "status": "available"},
]

TABLE_DISPLAY_NAMES = {
    "stock_daily_prices": "股票日线",
    "stock_valuation_daily": "估值快照",
    "index_daily": "指数日线",
    "sector_daily_prices": "行业日线",
    "sector_vendor_daily": "东财 BK 原始K线",
    "sector_composite_daily": "EvoAlpha 等权板块净值",
    "sector_membership_version": "板块成分版本",
    "sector_membership_item": "板块成分明细",
    "etf_daily_prices": "ETF 日线",
    "limit_board_trading": "涨跌停",
    "limit_down_trading": "跌停池",
    "limit_break_trading": "炸板池",
    "yesterday_limit_trading": "昨涨停池",
    "news_articles": "新闻公告",
    "macro_indicators": "宏观指标",
    "stock_finance_snapshot_tdx": "TDX 财务快照",
    "stock_f10_sections": "F10 文本缓存",
    "stock_financial_reports": "Sina 三表",
    "stock_research_reports": "个股研报",
    "industry_research_reports": "行业研报",
    "stock_announcements_cninfo": "巨潮公告",
    "stock_dragon_tiger_records": "龙虎榜",
    "stock_dragon_tiger_seats": "龙虎榜席位",
    "stock_lockup_expiry": "限售解禁",
    "stock_margin_trading": "融资融券",
    "stock_block_trades": "大宗交易",
    "stock_holder_num": "股东户数",
    "stock_dividend_history": "分红送转",
    "stock_fund_flow_daily": "资金流",
    "stock_concept_blocks": "概念板块",
    "market_hot_stocks": "个股热榜",
    "market_hot_concepts": "概念热榜",
    "northbound_flow_daily": "北向聚合",
    "quant_feature_rps": "个股 RPS",
    "quant_feature_sector_rps": "行业 RPS",
    "quant_feature_sector_rps_v2": "行业 RPS（等权净值）",
    "quant_feature_etf_rps": "ETF RPS",
    "market_strength_daily": "市场强度",
    "industry_strength_daily": "行业强度",
    "structure_health_daily": "结构健康",
    "stock_lifecycle_state": "生命周期",
    "timing_signals_daily": "买点信号",
    "watch_daily_snapshot": "低吸观察快照",
    "watch_events": "低吸状态事件",
    "notification_deliveries": "提醒投递记录",
    "trade_plans": "交易计划",
    "research_stock_daily_snapshot": "研究池快照",
}

SOURCE_COVERAGE_GROUPS = (
    {
        "id": "tdx_core",
        "name": "TDX / Mootdx 主链路",
        "source": "mootdx + local qfq",
        "scope": "全市场日K、RPS、生命周期",
        "next_run": "交易日 16:00 生产流水线",
        "tables": ("stock_daily_prices", "quant_feature_rps", "stock_lifecycle_state"),
    },
    {
        "id": "sector_composite",
        "name": "EvoAlpha 板块净值",
        "source": "local qfq stock_daily_prices + versioned membership",
        "scope": "行业趋势、行业 RPS、行业强度",
        "next_run": "交易日 daily；retry missing-only",
        "tables": ("sector_membership_version", "sector_membership_item", "sector_composite_daily", "quant_feature_sector_rps_v2"),
    },
    {
        "id": "eastmoney_sector_vendor",
        "name": "Eastmoney BK 原始K线",
        "source": "push2his + shared request gate",
        "scope": "真实 OHLC 参考，不阻断本地净值主链",
        "next_run": "daily 尾部 best-effort；熔断后跳过",
        "tables": ("sector_vendor_daily",),
    },
    {
        "id": "tencent_valuation",
        "name": "Tencent Direct 估值",
        "source": "qt.gtimg.cn",
        "scope": "全市场估值、换手、市值、涨跌停价",
        "next_run": "交易日估值任务；AkShare 兜底",
        "tables": ("stock_valuation_daily",),
    },
    {
        "id": "eastmoney_market",
        "name": "Eastmoney Direct 情绪",
        "source": "push2 / datacenter",
        "scope": "涨停、跌停、炸板、昨涨停、资金流、热榜",
        "next_run": "市场侧轻量任务 + 核心池刷新",
        "tables": (
            "limit_board_trading",
            "limit_down_trading",
            "limit_break_trading",
            "yesterday_limit_trading",
            "stock_fund_flow_daily",
            "market_hot_stocks",
            "market_hot_concepts",
        ),
    },
    {
        "id": "mootdx_f10",
        "name": "Mootdx 资料面",
        "source": "TDX F10 / finance",
        "scope": "核心池 F10 九大类、37 字段财务快照",
        "next_run": "核心池每日预取；非核心手动刷新",
        "tables": ("stock_finance_snapshot_tdx", "stock_f10_sections"),
    },
    {
        "id": "sina_finance",
        "name": "Sina Finance 三表",
        "source": "Sina direct",
        "scope": "利润表、资产负债表、现金流最近 8 期",
        "next_run": "核心池每日预取；非核心手动刷新",
        "tables": ("stock_financial_reports",),
    },
    {
        "id": "cninfo_announcements",
        "name": "CNInfo 公告",
        "source": "cninfo dynamic orgId",
        "scope": "巨潮公告缓存",
        "next_run": "核心池每日预取；非核心手动刷新",
        "tables": ("stock_announcements_cninfo",),
    },
    {
        "id": "risk_and_capital",
        "name": "风险 / 资金资料",
        "source": "Eastmoney + CNInfo",
        "scope": "龙虎榜、融资融券、大宗交易、股东户数、分红、解禁、北向聚合",
        "next_run": "核心池每日预取；重数据按需刷新",
        "tables": (
            "stock_dragon_tiger_records",
            "stock_dragon_tiger_seats",
            "stock_margin_trading",
            "stock_block_trades",
            "stock_holder_num",
            "stock_dividend_history",
            "stock_lockup_expiry",
            "northbound_flow_daily",
        ),
    },
)

PIPELINE_STAGE_DEFINITIONS = [
    {
        "step_key": "P0",
        "step_name": "预检查",
        "description": "数据库、任务入口与核心表可访问。",
        "critical_tables": ["stock_daily_prices"],
        "output_tables": ["local_quant.db"],
    },
    {
        "step_key": "P1",
        "step_name": "行情采集",
        "description": "股票、指数、行业、ETF 与涨跌停等行情数据。",
        "critical_tables": ["stock_daily_prices", "sector_composite_daily"],
        "optional_tables": ["index_daily", "sector_vendor_daily", "etf_daily_prices", "limit_board_trading"],
        "output_tables": ["stock_daily_prices", "sector_composite_daily", "sector_vendor_daily"],
    },
    {
        "step_key": "P2",
        "step_name": "清洗补齐",
        "description": "估值、宏观、新闻等辅助数据与基础清洗。",
        "critical_tables": ["stock_valuation_daily"],
        "optional_tables": ["macro_indicators", "news_articles"],
        "output_tables": ["stock_valuation_daily", "macro_indicators"],
    },
    {
        "step_key": "P3",
        "step_name": "RPS 计算",
        "description": "个股、行业、ETF 相对价格强度。",
        "critical_tables": ["quant_feature_rps", "quant_feature_sector_rps_v2"],
        "optional_tables": ["quant_feature_sector_rps", "quant_feature_etf_rps"],
        "output_tables": ["quant_feature_rps", "quant_feature_sector_rps_v2"],
    },
    {
        "step_key": "P4",
        "step_name": "市场/行业强度",
        "description": "M0-M4 市场状态与 I0-I4 行业状态。",
        "critical_tables": ["market_strength_daily", "industry_strength_daily"],
        "output_tables": ["market_strength_daily", "industry_strength_daily"],
    },
    {
        "step_key": "P5",
        "step_name": "结构/买点/生命周期",
        "description": "结构健康、买点信号、交易计划与生命周期。",
        "critical_tables": ["structure_health_daily", "stock_lifecycle_state"],
        "optional_tables": ["timing_signals_daily", "trade_plans"],
        "output_tables": ["structure_health_daily", "timing_signals_daily", "stock_lifecycle_state"],
    },
    {
        "step_key": "P6",
        "step_name": "研究池快照",
        "description": "研究池、主题池与机会池输出。",
        "critical_tables": [],
        "optional_tables": ["research_stock_daily_snapshot"],
        "output_tables": ["research_stock_daily_snapshot"],
    },
    {
        "step_key": "P7",
        "step_name": "云端同步",
        "description": "R2、云端库与前端展示层同步。",
        "critical_tables": [],
        "output_tables": ["R2", "cloud_db"],
        "sync_step": True,
    },
]

TASK_STATUS_MAP = {
    "queued": "PENDING",
    "running": "RUNNING",
    "success": "SUCCESS",
    "success_with_warnings": "WARNING",
    "check_only": "SUCCESS",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
}


class QuantTaskRunRequest(BaseModel):
    task_id: str
    params: dict[str, Any] = Field(default_factory=dict)


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail=f"本地数据库不存在: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _one(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _date_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)[:10]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _table_has_columns(conn: sqlite3.Connection, table_name: str, required: set[str]) -> bool:
    return required.issubset(_columns(conn, table_name))


def _latest_date(conn: sqlite3.Connection, table_name: str, date_column: str = "trade_date") -> str | None:
    if not _table_exists(conn, table_name):
        return None
    row = conn.execute(f"SELECT MAX({date_column}) AS max_date FROM {table_name}").fetchone()
    return _date_text(row["max_date"]) if row and row["max_date"] else None


def _latest_join_trade_date(
    conn: sqlite3.Connection,
    table_name: str,
    target_date: str,
) -> str | None:
    """Return the raw latest trade_date usable for exact joins.

    Some feature tables store dates as timestamps (for example
    "2026-07-03 00:00:00.000000").  Keep the raw value so large-table joins can
    use equality and indexes instead of DATE(column) scans.
    """
    if not _table_exists(conn, table_name):
        return None
    row = conn.execute(
        f"""
        SELECT MAX(trade_date) AS trade_date
        FROM {table_name}
        WHERE trade_date < DATE(?, '+1 day')
        """,
        (target_date,),
    ).fetchone()
    return str(row["trade_date"]) if row and row["trade_date"] else None


_trade_date_cache: dict[str, tuple[float, str]] = {}
_TRADE_DATE_CACHE_TTL = 300.0

# strategy page read endpoints are hit together on page load; keep a short
# in-process cache so refreshes and tab switches do not repeatedly scan SQLite.
_strategy_page_cache: dict[str, tuple[float, ApiResponse]] = {}
_STRATEGY_PAGE_CACHE_TTL = 60.0

# data-management 整页缓存（30 秒 TTL，避免前端频繁刷新重复扫 43 张表）
_dm_cache: dict[str, tuple[float, Any]] = {}
_DM_CACHE_TTL = 30.0


def _cache_get(cache: dict[str, tuple[float, Any]], key: str, ttl: float) -> Any | None:
    cached = cache.get(key)
    if not cached:
        return None
    cached_at, value = cached
    if time.time() - cached_at >= ttl:
        cache.pop(key, None)
        return None
    return value


def _cache_set(cache: dict[str, tuple[float, Any]], key: str, value: Any) -> Any:
    cache[key] = (time.time(), value)
    if len(cache) > 128:
        oldest_key = min(cache, key=lambda item: cache[item][0])
        cache.pop(oldest_key, None)
    return value


def _strategy_cache_key(*parts: object) -> str:
    return ":".join([str(DB_PATH), *(str(part) for part in parts)])


def _normalize_explicit_trade_date(trade_date: str) -> str:
    """Validate an explicitly requested date without changing its meaning."""
    try:
        return pd.to_datetime(trade_date).strftime("%Y-%m-%d")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无效交易日期: {trade_date}") from exc


def _resolve_trade_date(conn: sqlite3.Connection, trade_date: str | None) -> str:
    cache_key = trade_date or "__latest__"
    now = time.time()
    cached = _trade_date_cache.get(cache_key)
    if cached and (now - cached[0]) < _TRADE_DATE_CACHE_TTL:
        return cached[1]

    tables = (
        "stock_lifecycle_state",
        "timing_signals_daily",
        "market_strength_daily",
        "industry_strength_daily",
        "stock_daily_prices",
    )
    if trade_date:
        requested = _normalize_explicit_trade_date(trade_date)
        for table_name in tables:
            if not _table_exists(conn, table_name):
                continue
            row = conn.execute(
                f"""
                SELECT MAX(trade_date) AS trade_date
                FROM {table_name}
                WHERE trade_date <= ?
                """,
                (requested,),
            ).fetchone()
            if row and row["trade_date"]:
                result = str(row["trade_date"])
                _trade_date_cache[cache_key] = (now, result)
                return result
        _trade_date_cache[cache_key] = (now, requested)
        return requested

    for table_name in tables:
        latest = _latest_date(conn, table_name)
        if latest:
            _trade_date_cache[cache_key] = (now, latest)
            return latest
    raise HTTPException(status_code=404, detail="暂无可用交易日期")


def _limit(value: int, max_value: int = 500) -> int:
    return max(1, min(int(value), max_value))


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def _detect_date_column(columns: set[str]) -> str | None:
    for column in DATE_COLUMN_CANDIDATES:
        if column in columns:
            return column
    return None


def _table_summary(
    conn: sqlite3.Connection,
    table_name: str,
    date_column: str | None,
    key_columns: tuple[str, ...],
    include_duplicates: bool = True,
) -> dict[str, Any]:
    if not _table_exists(conn, table_name):
        return {
            "table_name": table_name,
            "exists": False,
            "critical": False,
            "date_column": date_column,
            "columns": [],
            "row_count": 0,
            "min_date": None,
            "max_date": None,
            "duplicate_groups": None,
            "duplicate_check_skipped": not include_duplicates,
            "status": "missing",
        }

    column_rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    columns = [str(row[1]) for row in column_rows]
    existing_columns = set(columns)

    # 优化：先用 MAX/MIN 查日期（走索引很快），COUNT(*) 只在需要时执行
    if date_column and date_column in existing_columns:
        date_row = conn.execute(
            f"SELECT MIN({date_column}) AS min_date, MAX({date_column}) AS max_date FROM {table_name}"
        ).fetchone()
        max_date = date_row["max_date"] if date_row else None
        min_date = date_row["min_date"] if date_row else None
        # 快速行数估算：对于大表 COUNT(*) 很慢，用 sqlite 的元数据
        # 但 sqlite 没有精确元数据行数，只能 COUNT
        # 折中：先查 MAX(date)，如果为空就是空表，跳过 COUNT
        if max_date is None:
            row_count = 0
        else:
            count_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table_name}").fetchone()
            row_count = count_row["cnt"] if count_row else 0
    else:
        count_row = conn.execute(f"SELECT COUNT(*) AS row_count FROM {table_name}").fetchone()
        row_count = count_row["row_count"] if count_row else 0
        date_column = None
        max_date = None
        min_date = None

    duplicate_groups: int | None = 0 if include_duplicates else None
    if include_duplicates and key_columns and set(key_columns).issubset(existing_columns):
        group_by = ", ".join(key_columns)
        duplicate_groups = conn.execute(
            f"""
            SELECT COUNT(*) AS duplicate_groups
            FROM (
                SELECT {group_by}, COUNT(*) AS c
                FROM {table_name}
                GROUP BY {group_by}
                HAVING c > 1
            )
            """
        ).fetchone()["duplicate_groups"]

    status = "ok"
    if duplicate_groups:
        status = "duplicate"
    elif row_count == 0:
        status = "empty"

    return {
        "table_name": table_name,
        "exists": True,
        "date_column": date_column,
        "columns": columns,
        "row_count": row_count,
        "min_date": _date_text(min_date) if date_column and min_date else None,
        "max_date": _date_text(max_date),
        "duplicate_groups": duplicate_groups,
        "duplicate_check_skipped": not include_duplicates,
        "status": status,
    }


def _apply_lag_status(row: dict[str, Any], latest_stock_date: str | None) -> None:
    if row.get("max_date") and latest_stock_date and row["table_name"] != "news_articles":
        delta = (pd.to_datetime(latest_stock_date) - pd.to_datetime(row["max_date"])).days
        row["ahead_days"] = abs(delta) if delta < 0 else 0
        row["lag_days"] = max(delta, 0)
        if row["critical"] and row["lag_days"] > 1 and row["status"] == "ok":
            row["status"] = "stale"
    else:
        row["ahead_days"] = None
        row["lag_days"] = None


def _sort_direction(sort_dir: str | None) -> str:
    return "ASC" if str(sort_dir or "").lower() == "asc" else "DESC"


def _rps_period(value: int | str | None, default: int = 20) -> int:
    try:
        period = int(str(value or default).replace("rps_", ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无效 RPS 周期: {value}") from exc
    if period not in RPS_PERIODS:
        raise HTTPException(status_code=400, detail=f"不支持的 RPS 周期: {period}")
    return period


def _lifecycle_sort_column(sort_by: str | None) -> str:
    allowed = {
        "state_rank",
        "structure_score",
        "lifecycle_state",
        "market_state",
        "industry_state",
        *RPS_COLUMNS,
    }
    column = str(sort_by or "state_rank")
    if column not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的生命周期排序字段: {column}")
    return column


def _industry_sort_column(sort_by: str | None) -> str:
    allowed = {
        "industry_strength_score",
        "return_5d",
        "return_20d",
        "return_50d",
        "return_120d",
        "amount",
        "amount_ratio_20",
        "rps90_stock_count",
        "rps90_stock_ratio",
        "new_high_stock_count",
        "new_high_stock_ratio",
        "leader_strength_score",
        *SECTOR_RPS_COLUMNS,
    }
    column = str(sort_by or "industry_strength_score")
    if column not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的行业排序字段: {column}")
    return column


def _industry_stock_sort_column(sort_by: str | None, period: int) -> str:
    allowed = {
        "symbol",
        "name",
        "stock_info_industry",
        "market_state",
        "industry_state",
        "signal_type",
        "close",
        "pct_chg",
        "amount",
        "volume",
        "state_rank",
        "structure_score",
        "lifecycle_state",
        *RPS_COLUMNS,
    }
    column = str(sort_by or f"rps_{period}")
    if column not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的行业个股排序字段: {column}")
    return column


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if pd.notna(parsed) else None


def _ema(values: list[float | None], span: int) -> list[float | None]:
    alpha = 2 / (span + 1)
    result: list[float | None] = []
    previous: float | None = None
    for value in values:
        if value is None:
            result.append(previous)
            continue
        previous = value if previous is None else previous * (1 - alpha) + value * alpha
        result.append(round(previous, 4))
    return result


def _calculate_macd(rows: list[dict[str, Any]]) -> dict[str, list[float | None]]:
    closes = [_float_or_none(row.get("close")) for row in rows]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    diff: list[float | None] = []
    dea_values: list[float | None] = []
    hist: list[float | None] = []
    dea: float | None = None
    alpha = 2 / (9 + 1)

    for short, long in zip(ema12, ema26):
        if short is None or long is None:
            diff.append(None)
            dea_values.append(dea)
            hist.append(None)
            continue
        dif = short - long
        dea = dif if dea is None else dea * (1 - alpha) + dif * alpha
        diff.append(round(dif, 4))
        dea_values.append(round(dea, 4))
        hist.append(round((dif - dea) * 2, 4))

    return {"diff": diff, "dea": dea_values, "hist": hist}


def _calculate_kdj(
    rows: list[dict[str, Any]],
    period: int = 9,
) -> dict[str, list[float | None]]:
    k_values: list[float | None] = []
    d_values: list[float | None] = []
    j_values: list[float | None] = []
    k = 50.0
    d = 50.0

    for index, row in enumerate(rows):
        window = rows[max(0, index - period + 1): index + 1]
        highs = [_float_or_none(item.get("high")) for item in window]
        lows = [_float_or_none(item.get("low")) for item in window]
        valid_highs = [value for value in highs if value is not None]
        valid_lows = [value for value in lows if value is not None]
        close = _float_or_none(row.get("close"))

        if not valid_highs or not valid_lows or close is None:
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            continue

        highest = max(valid_highs)
        lowest = min(valid_lows)
        rsv = 50.0 if highest == lowest else (close - lowest) / (highest - lowest) * 100
        k = (2 / 3) * k + (1 / 3) * rsv
        d = (2 / 3) * d + (1 / 3) * k
        j = 3 * k - 2 * d
        k_values.append(round(k, 4))
        d_values.append(round(d, 4))
        j_values.append(round(j, 4))

    return {"k": k_values, "d": d_values, "j": j_values}


def _sector_chart_series(
    rows: list[dict[str, Any]],
    *,
    tail_limit: int | None = None,
) -> dict[str, Any]:
    macd = _calculate_macd(rows)
    kdj = _calculate_kdj(rows)
    for index, row in enumerate(rows):
        row["macd_diff"] = macd["diff"][index]
        row["macd_dea"] = macd["dea"][index]
        row["macd_hist"] = macd["hist"][index]
        row["kdj_k"] = kdj["k"][index]
        row["kdj_d"] = kdj["d"][index]
        row["kdj_j"] = kdj["j"][index]

    start = max(0, len(rows) - tail_limit) if tail_limit else 0
    visible_rows = rows[start:]
    sources: dict[str, int] = {}
    synthetic_rows = 0
    for row in visible_rows:
        source = str(row.get("source") or "unknown")
        sources[source] = sources.get(source, 0) + 1
        if source == "synthetic_stock_map" or str(row.get("quality_flag") or "") == "synthetic":
            synthetic_rows += 1

    def tail(values: list[Any]) -> list[Any]:
        return values[start:]

    return {
        "dates": [row.get("trade_date") for row in visible_rows],
        "kline": [
            [row.get("open"), row.get("close"), row.get("low"), row.get("high")]
            for row in visible_rows
        ],
        "volume": [row.get("volume") for row in visible_rows],
        "amount": [row.get("amount") for row in visible_rows],
        "pct_chg": [row.get("pct_chg") for row in visible_rows],
        "macd": {key: tail(values) for key, values in macd.items()},
        "kdj": {key: tail(values) for key, values in kdj.items()},
        "rps_short": {
            "r5": [row.get("rps_5") for row in visible_rows],
            "r10": [row.get("rps_10") for row in visible_rows],
            "r20": [row.get("rps_20") for row in visible_rows],
        },
        "rps_long": {
            "r50": [row.get("rps_50") for row in visible_rows],
            "r120": [row.get("rps_120") for row in visible_rows],
            "r250": [row.get("rps_250") for row in visible_rows],
        },
        "quality_summary": {
            "rows": len(visible_rows),
            "synthetic_rows": synthetic_rows,
            "real_rows": len(visible_rows) - synthetic_rows,
            "sources": sources,
        },
        "rows": visible_rows,
    }


def _sector_composite_chart_series(
    rows: list[dict[str, Any]],
    *,
    tail_limit: int | None = None,
) -> dict[str, Any]:
    """Build chart series for the canonical net-value track.

    A composite has no true daily open/high/low.  MACD is calculated from the
    net value, while KDJ and candle payloads are deliberately omitted.
    """
    start = max(0, len(rows) - tail_limit) if tail_limit else 0
    visible_rows = rows[start:]
    macd_rows = [
        {"close": _float_or_none(row.get("net_value"))}
        for row in rows
    ]
    macd = _calculate_macd(macd_rows)

    def tail(values: list[Any]) -> list[Any]:
        return values[start:]

    sources: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    for row in visible_rows:
        source = str(row.get("source") or "local_equal_weight")
        quality = str(row.get("quality_flag") or "unknown")
        sources[source] = sources.get(source, 0) + 1
        quality_counts[quality] = quality_counts.get(quality, 0) + 1

    return {
        "dates": [row.get("trade_date") for row in visible_rows],
        "net_value": [row.get("net_value") for row in visible_rows],
        "kline": [],
        "volume": [row.get("volume") for row in visible_rows],
        "amount": [row.get("amount") for row in visible_rows],
        "pct_chg": [row.get("pct_chg") for row in visible_rows],
        "macd": {key: tail(values) for key, values in macd.items()},
        "kdj": {"k": [], "d": [], "j": []},
        "rps_short": {
            "r5": [row.get("rps_5") for row in visible_rows],
            "r10": [row.get("rps_10") for row in visible_rows],
            "r20": [row.get("rps_20") for row in visible_rows],
        },
        "rps_long": {
            "r50": [row.get("rps_50") for row in visible_rows],
            "r120": [row.get("rps_120") for row in visible_rows],
            "r250": [row.get("rps_250") for row in visible_rows],
        },
        "quality_summary": {
            "rows": len(visible_rows),
            "sources": sources,
            "quality_flags": quality_counts,
            "synthetic_rows": 0,
            "real_rows": len(visible_rows),
        },
        "rows": visible_rows,
    }


def _stock_rps_expr(period: int, has_rps_table: bool) -> str:
    if not has_rps_table:
        return f"l.rps_{period}" if period in {20, 50, 120, 250} else "NULL"
    if period in {20, 50, 120, 250}:
        return f"COALESCE(r.rps_{period}, l.rps_{period})"
    return f"r.rps_{period}"


def _read_progress_files() -> list[dict[str, Any]]:
    progress_dirs = [
        BACKEND_DIR / "data" / "collection_progress",
        BACKEND_DIR / "data_job" / "data" / "collection_progress",
    ]
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for progress_dir in progress_dirs:
        if not progress_dir.exists():
            continue
        for path in sorted(progress_dir.glob("*.json")):
            if path in seen:
                continue
            seen.add(path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                payload = {"error": str(exc)}
            stat = path.stat()
            rows.append(
                {
                    "collector": path.stem,
                    "path": str(path.relative_to(BACKEND_DIR)),
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "last_date": payload.get("last_date") or payload.get("end_date"),
                    "status": payload.get("status") or ("error" if payload.get("error") else "tracked"),
                    "records": payload.get("records") or payload.get("total_records") or payload.get("success_count"),
                    "error": payload.get("error"),
                }
            )
    return rows


_workflow_plan_cache: list[dict[str, Any]] | None = None


def _workflow_plan() -> list[dict[str, Any]]:
    global _workflow_plan_cache
    if _workflow_plan_cache is not None:
        return _workflow_plan_cache
    try:
        from scripts.run_daily_data_workflow import build_parser, make_tasks

        args = build_parser().parse_args(["--check-only"])
        result = [
            {
                "index": index,
                "name": task.name,
                "critical": task.critical,
                "command": " ".join(task.command),
            }
            for index, task in enumerate(make_tasks(args), 1)
        ]
        _workflow_plan_cache = result
        return result
    except Exception as exc:
        return [{"index": 1, "name": "workflow_plan_unavailable", "critical": False, "error": str(exc)}]


def _recent_log_files(limit: int = 8) -> list[dict[str, Any]]:
    log_dir = BACKEND_DIR / "logs"
    if not log_dir.exists():
        return []
    rows = []
    for path in sorted(log_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        stat = path.stat()
        rows.append(
            {
                "name": path.name,
                "path": str(path.relative_to(BACKEND_DIR)),
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _normalize_task_status(status: Any) -> str:
    raw = str(status or "unknown").lower()
    return TASK_STATUS_MAP.get(raw, raw.upper())


def _task_progress(status: Any) -> int:
    normalized = _normalize_task_status(status)
    if normalized == "SUCCESS":
        return 100
    if normalized == "WARNING":
        return 88
    if normalized == "RUNNING":
        return 48
    if normalized == "PENDING":
        return 8
    if normalized == "FAILED":
        return 100
    return 0


def _stage_progress(status: str) -> int:
    return {
        "SUCCESS": 100,
        "WARNING": 82,
        "FAILED": 100,
        "SKIPPED": 0,
        "PENDING": 0,
        "RUNNING": 52,
    }.get(status, 0)


def _table_health_rows(table_rows: list[dict[str, Any]], latest_stock_date: str | None) -> list[dict[str, Any]]:
    health_rows = []
    for row in table_rows:
        table_name = str(row.get("table_name") or "")
        raw_status = str(row.get("status") or "unknown")
        duplicate_groups = int(row.get("duplicate_groups") or 0)
        lag_days = row.get("lag_days")
        if raw_status in {"missing", "empty"}:
            health_status = "MISSING"
        elif raw_status == "duplicate" or duplicate_groups > 0:
            health_status = "ERROR"
        elif raw_status == "stale":
            health_status = "STALE"
        elif isinstance(lag_days, int) and lag_days > 0 and row.get("critical"):
            health_status = "STALE"
        else:
            health_status = "OK"

        enhanced = dict(row)
        enhanced.update(
            {
                "display_name": TABLE_DISPLAY_NAMES.get(table_name, table_name),
                "latest_trade_date": row.get("max_date"),
                "expected_latest_date": latest_stock_date,
                "missing_days": lag_days if lag_days is not None else None,
                "anomaly_count": duplicate_groups,
                "health_status": health_status,
                "last_updated_at": row.get("max_date") or row.get("min_date"),
            }
        )
        health_rows.append(enhanced)
    return health_rows


def _source_coverage_rows(table_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_map = {str(row.get("table_name")): row for row in table_rows}
    coverage_rows: list[dict[str, Any]] = []
    for group in SOURCE_COVERAGE_GROUPS:
        tables = []
        total_rows = 0
        latest_dates: list[str] = []
        missing = 0
        empty = 0
        stale = 0
        for table_name in group["tables"]:
            row = table_map.get(table_name)
            if not row:
                missing += 1
                tables.append(
                    {
                        "table_name": table_name,
                        "display_name": TABLE_DISPLAY_NAMES.get(table_name, table_name),
                        "status": "missing",
                        "row_count": 0,
                        "max_date": None,
                    }
                )
                continue
            row_count = int(row.get("row_count") or 0)
            total_rows += row_count
            status = str(row.get("status") or "unknown")
            if status == "empty":
                empty += 1
            elif status == "stale":
                stale += 1
            max_date = row.get("max_date")
            if max_date:
                latest_dates.append(str(max_date))
            tables.append(
                {
                    "table_name": table_name,
                    "display_name": TABLE_DISPLAY_NAMES.get(table_name, table_name),
                    "status": status,
                    "row_count": row_count,
                    "max_date": max_date,
                }
            )

        if missing == len(group["tables"]):
            status = "missing"
        elif total_rows == 0:
            status = "empty"
        elif stale:
            status = "stale"
        else:
            status = "ok"

        coverage_rows.append(
            {
                **group,
                "status": status,
                "row_count": total_rows,
                "latest_date": max(latest_dates) if latest_dates else None,
                "missing_tables": missing,
                "empty_tables": empty,
                "stale_tables": stale,
                "tables": tables,
            }
        )
    return coverage_rows


def _table_ready(table_map: dict[str, dict[str, Any]], table_name: str, max_lag_days: int = 0) -> bool:
    row = table_map.get(table_name)
    if not row or not row.get("exists") or not row.get("max_date"):
        return False
    if str(row.get("status")) in {"missing", "empty", "duplicate"}:
        return False
    lag_days = row.get("lag_days")
    return not isinstance(lag_days, int) or lag_days <= max_lag_days


def _stage_from_tables(
    definition: dict[str, Any],
    table_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if definition.get("sync_step"):
        r2_ready = bool(settings.R2_ACCOUNT_ID and settings.R2_BUCKET_NAME)
        cloud_ready = bool(settings.CLOUD_DATABASE_URL)
        status = "SUCCESS" if r2_ready and cloud_ready else "SKIPPED"
        message = "同步配置已就绪" if status == "SUCCESS" else "云端同步未配置或未启用"
        return {
            **definition,
            "status": status,
            "progress": _stage_progress(status),
            "message": message,
            "error": None,
        }

    critical_tables = list(definition.get("critical_tables") or [])
    optional_tables = list(definition.get("optional_tables") or [])
    missing = [
        table
        for table in critical_tables
        if not table_map.get(table)
        or not table_map[table].get("exists")
        or str(table_map[table].get("status")) in {"missing", "empty"}
    ]
    stale = [
        table
        for table in [*critical_tables, *optional_tables]
        if table_map.get(table) and isinstance(table_map[table].get("lag_days"), int)
        and int(table_map[table]["lag_days"]) > 0
    ]
    duplicates = [
        table
        for table in [*critical_tables, *optional_tables]
        if table_map.get(table) and int(table_map[table].get("duplicate_groups") or 0) > 0
    ]
    optional_missing = [
        table
        for table in optional_tables
        if not table_map.get(table)
        or not table_map[table].get("exists")
        or str(table_map[table].get("status")) in {"missing", "empty"}
    ]

    if missing:
        status = "FAILED"
        message = f"关键输出缺失：{', '.join(missing)}"
    elif duplicates:
        status = "WARNING"
        message = f"存在重复键：{', '.join(duplicates)}"
    elif stale:
        status = "WARNING"
        message = f"数据滞后：{', '.join(stale)}"
    elif optional_missing:
        status = "WARNING"
        message = f"可选输出未生成：{', '.join(optional_missing)}"
    else:
        status = "SUCCESS"
        message = "阶段输出已就绪"

    return {
        **definition,
        "status": status,
        "progress": _stage_progress(status),
        "message": message,
        "error": message if status == "FAILED" else None,
    }


def _build_pipeline_stages(table_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_map = {str(row.get("table_name")): row for row in table_rows}
    return [_stage_from_tables(definition, table_map) for definition in PIPELINE_STAGE_DEFINITIONS]


def _read_pipeline_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    if not _table_exists(conn, "pipeline_run_log"):
        return []
    rows = conn.execute(
        """
        SELECT run_id, pipeline_name, mode, status, started_at, finished_at,
               ROUND(elapsed_seconds, 1) AS elapsed_seconds,
               critical_failed_tasks, failed_tasks, log_path
        FROM pipeline_run_log
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return _rows(rows)


def _read_data_core_quality(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        reports = latest_quality_reports(conn)
        source_failures = latest_source_failures(conn)
    except Exception:
        reports = []
        source_failures = []

    status_counts: dict[str, int] = {}
    for report in reports:
        status = str(report.get("status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "reports": reports,
        "status_counts": status_counts,
        "source_failures": source_failures,
    }


def _summarize_data_core_quality(data: dict[str, Any], limit: int = 12) -> dict[str, Any]:
    reports = list(data.get("reports") or [])
    source_failures = list(data.get("source_failures") or [])
    compact_reports = []
    for report in reports[:limit]:
        compact_reports.append(
            {
                "run_id": report.get("run_id"),
                "trade_date": report.get("trade_date"),
                "module": report.get("module"),
                "market": report.get("market"),
                "status": report.get("status"),
                "quality_score": report.get("quality_score"),
                "missing_count": report.get("missing_count"),
                "abnormal_count": report.get("abnormal_count"),
                "duplicate_count": report.get("duplicate_count"),
                "created_at": report.get("created_at"),
            }
        )

    compact_failures = []
    for failure in source_failures[:limit]:
        message = str(failure.get("error_message") or "")
        compact_failures.append(
            {
                "source": failure.get("source"),
                "api_name": failure.get("api_name"),
                "market": failure.get("market"),
                "status": failure.get("status"),
                "fail_count": failure.get("fail_count"),
                "error_message": message[:240],
                "created_at": failure.get("created_at"),
            }
        )

    return {
        "report_count": len(reports),
        "source_failure_count": len(source_failures),
        "status_counts": data.get("status_counts") or {},
        "reports": compact_reports,
        "source_failures": compact_failures,
    }


def _read_data_core_quality_summary(conn: sqlite3.Connection, limit: int = 12) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    source_failures: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    if _table_exists(conn, "data_quality_report"):
        reports = _rows(conn.execute(
            """
            SELECT run_id, trade_date, module, market, status, quality_score,
                   missing_count, abnormal_count, duplicate_count, created_at
            FROM data_quality_report
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall())
        for row in conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM data_quality_report
            GROUP BY status
            """
        ).fetchall():
            status_counts[str(row["status"] or "UNKNOWN")] = int(row["count"] or 0)

    if _table_exists(conn, "data_source_log"):
        source_failures = _rows(conn.execute(
            """
            SELECT source, api_name, market, status, fail_count,
                   SUBSTR(COALESCE(error_message, ''), 1, 240) AS error_message,
                   created_at
            FROM data_source_log
            WHERE status <> 'SUCCESS'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall())

    return {
        "report_count": len(reports),
        "source_failure_count": len(source_failures),
        "status_counts": status_counts,
        "reports": reports,
        "source_failures": source_failures,
    }


def _read_log_tail(raw_path: Any, limit: int = 80) -> list[dict[str, Any]]:
    if not raw_path:
        return []

    path = Path(str(raw_path))
    if not path.is_absolute():
        path = BACKEND_DIR / path

    try:
        resolved = path.resolve()
        backend = BACKEND_DIR.resolve()
        if resolved != backend and backend not in resolved.parents:
            return []
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except Exception:
        return []

    result = []
    for index, line in enumerate(lines, 1):
        level = "info"
        upper = line.upper()
        if "ERROR" in upper or "FAILED" in upper or "失败" in line:
            level = "error"
        elif "WARN" in upper or "WARNING" in upper or "跳过" in line:
            level = "warning"
        result.append({"index": index, "level": level, "message": line})
    return result


def _latest_started_at(row: dict[str, Any]) -> str:
    return str(row.get("started_at") or row.get("created_at") or "")


def _latest_job_run(
    task_runs: list[dict[str, Any]],
    pipeline_runs: list[dict[str, Any]],
    pipeline_stages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    if task_runs:
        candidates.append(("manual", _latest_started_at(task_runs[0]), task_runs[0]))
    if pipeline_runs:
        candidates.append(("pipeline", _latest_started_at(pipeline_runs[0]), pipeline_runs[0]))
    if not candidates:
        return None

    run_type, _, row = sorted(candidates, key=lambda item: item[1], reverse=True)[0]
    status = _normalize_task_status(row.get("status"))
    log_lines = _read_log_tail(row.get("log_path"))

    if run_type == "pipeline":
        failed_tasks = [
            item for item in str(row.get("failed_tasks") or "").split(",") if item.strip()
        ]
        current_step = failed_tasks[0] if failed_tasks else None
        return {
            "run_id": row.get("run_id"),
            "job_key": row.get("pipeline_name") or "daily_data_workflow",
            "job_name": "完整生产流水线",
            "status": status,
            "progress": _task_progress(status),
            "started_at": row.get("started_at"),
            "ended_at": row.get("finished_at"),
            "elapsed_seconds": row.get("elapsed_seconds"),
            "current_step": current_step,
            "message": row.get("mode"),
            "error": row.get("critical_failed_tasks") or None,
            "steps": pipeline_stages,
            "logs": log_lines,
        }

    step = {
        "step_key": str(row.get("task_id") or "manual_task"),
        "step_name": row.get("task_title") or row.get("task_id"),
        "status": status,
        "progress": _task_progress(status),
        "started_at": row.get("started_at"),
        "ended_at": row.get("finished_at"),
        "duration_ms": (
            int(float(row["elapsed_seconds"]) * 1000)
            if row.get("elapsed_seconds") is not None
            else None
        ),
        "output_tables": [],
        "message": row.get("command"),
        "error": row.get("error"),
    }
    return {
        "run_id": row.get("run_id"),
        "job_key": row.get("task_id"),
        "job_name": row.get("task_title") or row.get("task_id"),
        "status": status,
        "progress": _task_progress(status),
        "started_at": row.get("started_at"),
        "ended_at": row.get("finished_at"),
        "elapsed_seconds": row.get("elapsed_seconds"),
        "current_step": row.get("task_title") or row.get("task_id"),
        "message": row.get("command"),
        "error": row.get("error"),
        "steps": [step],
        "logs": log_lines,
    }


def _decorate_data_sources(task_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decorated = []
    for source in DATA_SOURCES:
        last_success = None
        failure_count = 0
        source_id = source["id"]
        for run in task_runs:
            params = run.get("params") or {}
            if params.get("source") != source_id:
                continue
            if run.get("status") == "failed":
                failure_count += 1
            if run.get("status") == "success" and not last_success:
                last_success = run.get("finished_at") or run.get("started_at")
        decorated.append(
            {
                **source,
                "last_success_at": last_success,
                "failure_count": failure_count,
                "is_primary": bool(source.get("production_enabled")),
            }
        )
    return decorated


def _build_system_status(
    latest_stock_date: str | None,
    table_rows: list[dict[str, Any]],
    pipeline_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    table_map = {str(row.get("table_name")): row for row in table_rows}
    stock_ready = _table_ready(table_map, "stock_daily_prices")
    # The versioned equal-weight composite RPS table is the production
    # industry input.  Keep the legacy table visible for diagnostics, but do
    # not let it make the system appear ready after the canonical table is
    # missing or stale.
    sector_rps_table = (
        "quant_feature_sector_rps_v2"
        if "quant_feature_sector_rps_v2" in table_map
        else "quant_feature_sector_rps"
    )
    rps_ready = _table_ready(table_map, "quant_feature_rps") and _table_ready(
        table_map,
        sector_rps_table,
    )
    market_ready = _table_ready(table_map, "market_strength_daily")
    industry_ready = _table_ready(table_map, "industry_strength_daily")
    lifecycle_ready = _table_ready(table_map, "stock_lifecycle_state")
    cloud_ready = bool(settings.CLOUD_DATABASE_URL)
    r2_ready = bool(settings.R2_ACCOUNT_ID and settings.R2_BUCKET_NAME)

    blockers = []
    warnings = []
    if not DB_PATH.exists():
        blockers.append("本地 SQLite 数据库不存在")
    if not latest_stock_date or not stock_ready:
        blockers.append("股票行情未更新到最新本地交易日")
    if not rps_ready:
        blockers.append("RPS 核心因子未完成")
    if not market_ready:
        warnings.append("市场强度未生成或已滞后")
    if not industry_ready:
        warnings.append("行业强度未生成或已滞后")
    if not lifecycle_ready:
        warnings.append("生命周期状态未生成或已滞后")
    if not cloud_ready:
        warnings.append("云端数据库未配置，前端云端展示不可用")
    if not r2_ready:
        warnings.append("R2 未配置，文件同步不可用")

    latest_pipeline = pipeline_runs[0] if pipeline_runs else None
    latest_pipeline_failed = latest_pipeline and str(latest_pipeline.get("status")) == "failed"
    if latest_pipeline_failed:
        warnings.append("最近一次生产流水线失败，请查看日志")

    if blockers:
        status = "ERROR" if latest_pipeline_failed else "BLOCKED"
        summary = "核心行情或 RPS 未完成，系统不可用。"
    elif warnings:
        status = "PARTIAL"
        summary = "核心数据可用，但仍有同步或增强输出未完成。"
    else:
        status = "READY"
        summary = "今日核心链路已完成，系统可用于 Market / Industries / Alpha Pool。"

    return {
        "status": status,
        "trade_date": latest_stock_date,
        "latest_local_date": latest_stock_date,
        "market_ready": market_ready,
        "industry_ready": industry_ready,
        "rps_ready": rps_ready,
        "lifecycle_ready": lifecycle_ready,
        "cloud_sync_ready": cloud_ready and r2_ready,
        "r2_ready": r2_ready,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _ensure_task_run_table() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quant_task_run_log (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                task_title TEXT,
                status TEXT NOT NULL,
                command TEXT NOT NULL,
                params_json TEXT,
                pid INTEGER,
                returncode INTEGER,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                elapsed_seconds REAL,
                log_path TEXT,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def _task_title(task_id: str) -> str:
    for task in TASK_CATALOG:
        if task["id"] == task_id:
            return str(task["title"])
    return task_id


def _latest_stock_date_safe() -> str:
    try:
        with _connect() as conn:
            return _latest_date(conn, "stock_daily_prices") or datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _date_param(params: dict[str, Any], key: str, default: str | None = None) -> str:
    value = params.get(key) or default or datetime.now().strftime("%Y-%m-%d")
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无效日期参数 {key}: {value}") from exc


def _int_param(params: dict[str, Any], key: str, default: int, min_value: int, max_value: int) -> int:
    value = params.get(key, default)
    try:
        parsed = int(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无效整数参数 {key}: {value}") from exc
    return max(min_value, min(parsed, max_value))


def _bool_param(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return bool(value)


def _safe_symbol(value: Any) -> str | None:
    if not value:
        return None
    symbol = str(value).strip()
    if not symbol:
        return None
    if not symbol.isdigit() or len(symbol) > 6:
        raise HTTPException(status_code=400, detail=f"无效股票代码: {symbol}")
    return symbol.zfill(6)


def _build_mootdx_command(params: dict[str, Any], *, backfill: bool) -> list[str]:
    source = str(params.get("source") or "tdx_mootdx_qfq")
    if source == "mootdx_qfq":
        source = "tdx_mootdx_qfq"

    source_def = get_source(source)
    if source_def and not source_def.allowed_official_write:
        raise HTTPException(
            status_code=400,
            detail=f"{source} 是 {source_def.role} 源，不允许写正式日K",
        )
    if source == "tdx_exhq":
        raise HTTPException(status_code=400, detail="tdx_exhq 当前只用于 BSE capability，不作为手动下载入口")
    if not is_official_write_source(source, MODULE_STOCK_DAILY):
        raise HTTPException(status_code=400, detail=f"不支持的数据源: {source}")

    latest = _latest_stock_date_safe()
    end_date = _date_param(params, "end_date", latest)
    command = [
        sys.executable,
        "scripts/standalone/collect_stock_kline_mootdx_qfq.py",
        "--end",
        end_date,
        "--workers",
        str(_int_param(params, "workers", 8, 1, 24)),
        "--pool-size",
        str(_int_param(params, "pool_size", 8, 1, 12)),
        "--batch-size",
        str(_int_param(params, "batch_size", 80, 10, 500)),
        "--lookback-days",
        str(_int_param(params, "lookback_days", 7, 1, 120)),
        "--max-bars",
        str(_int_param(params, "max_bars", 160, 1, 2000)),
        "--submit-window",
        str(_int_param(params, "submit_window", 256, 1, 10000)),
    ]

    market = str(params.get("market") or "ALL").strip().upper()
    if market not in {"ALL", "SH", "SZ", "BSE"}:
        raise HTTPException(status_code=400, detail=f"无效市场参数 market: {market}")
    command.extend(["--market", market])

    if _bool_param(params, "diagnostic_only"):
        command.append("--diagnostic-only")

    if backfill:
        start_date = _date_param(params, "start_date", end_date)
        if pd.to_datetime(start_date) > pd.to_datetime(end_date):
            raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
        command.extend(["--start", start_date])
        if _bool_param(params, "full_refresh"):
            command.append("--full-refresh")

    symbol = _safe_symbol(params.get("symbol"))
    if symbol:
        command.extend(["--symbol", symbol])
    elif params.get("limit"):
        command.extend(["--limit", str(_int_param(params, "limit", 50, 1, 6000))])

    return command


def _calculator_names(params: dict[str, Any], default: list[str], allowed: set[str]) -> list[str]:
    raw = params.get("calculators") or default
    if isinstance(raw, str):
        names = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        names = [str(item).strip() for item in raw if str(item).strip()]
    else:
        names = default
    invalid = [name for name in names if name not in allowed]
    if invalid:
        raise HTTPException(status_code=400, detail=f"不支持的计算器: {', '.join(invalid)}")
    return names or default


def _build_task_command(task_id: str, params: dict[str, Any]) -> list[str]:
    if task_id == "daily_kline_download":
        return _build_mootdx_command(params, backfill=False)
    if task_id == "kline_backfill":
        return _build_mootdx_command(params, backfill=True)
    if task_id == "rps_calculation":
        names = _calculator_names(params, ["stock", "sector", "etf"], {"stock", "sector", "etf"})
        return [
            sys.executable,
            "-m",
            "quant_engine.runner.feature_runner",
            "--mode",
            str(params.get("mode") or "daily"),
            "--calculators",
            *names,
        ]
    if task_id == "p0_calculation":
        names = _calculator_names(params, ["strength", "structure", "lifecycle"], {"strength", "structure", "lifecycle"})
        return [sys.executable, "-m", "quant_engine.runner.feature_runner", "--mode", "daily", "--calculators", *names]
    if task_id == "p1_calculation":
        names = _calculator_names(params, ["timing", "risk", "research"], {"timing", "risk", "research"})
        return [sys.executable, "-m", "quant_engine.runner.feature_runner", "--mode", "daily", "--calculators", *names]
    if task_id == "mrgc_recalculation":
        if not params.get("trade_date"):
            raise HTTPException(status_code=400, detail="MRGC 重算必须指定 trade_date")
        trade_date = _date_param(params, "trade_date")
        return [
            sys.executable,
            "-m",
            "quant_engine.runner.strategy_runner",
            "--strategy",
            "mrgc",
            "--date",
            trade_date,
        ]
    if task_id == "kline_then_rps":
        command = [sys.executable, "scripts/run_daily_data_workflow.py", "--skip-market-side", "--skip-p0", "--skip-p1"]
        if params.get("end_date"):
            command.extend(["--kline-end", _date_param(params, "end_date")])
        return command
    if task_id == "daily_workflow":
        command = [sys.executable, "scripts/run_daily_data_workflow.py"]
        if params.get("end_date"):
            command.extend(["--kline-end", _date_param(params, "end_date")])
        return command
    if task_id == "workflow_check":
        return [sys.executable, "scripts/run_daily_data_workflow.py", "--check-only"]
    raise HTTPException(status_code=404, detail=f"未知任务: {task_id}")


def _read_task_runs(limit: int = 20) -> list[dict[str, Any]]:
    _ensure_task_run_table()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT run_id, task_id, task_title, status, command, params_json, pid,
                   returncode, started_at, finished_at, ROUND(elapsed_seconds, 1) AS elapsed_seconds,
                   log_path, error
            FROM quant_task_run_log
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["params"] = json.loads(item.pop("params_json") or "{}")
        except Exception:
            item["params"] = {}
        result.append(item)
    return result


def _run_quant_task(run_id: str, command: list[str], params: dict[str, Any], log_path: Path) -> None:
    start = time.perf_counter()
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(BACKEND_DIR) if not pythonpath else f"{BACKEND_DIR}:{pythonpath}"

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE quant_task_run_log SET status = 'running' WHERE run_id = ?",
            (run_id,),
        )
        conn.commit()

    returncode = -1
    error = None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write(f"run_id={run_id}\n")
            log_file.write(f"started_at={datetime.now():%Y-%m-%d %H:%M:%S}\n")
            log_file.write(f"cwd={BACKEND_DIR}\n")
            log_file.write(f"command={' '.join(command)}\n")
            log_file.write(f"params={json.dumps(params, ensure_ascii=False)}\n\n")
            log_file.flush()
            process = subprocess.Popen(
                command,
                cwd=BACKEND_DIR,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE quant_task_run_log SET pid = ? WHERE run_id = ?",
                    (process.pid, run_id),
                )
                conn.commit()
            returncode = process.wait()
    except Exception as exc:
        error = str(exc)

    status = "success" if returncode == 0 and not error else "failed"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE quant_task_run_log
            SET status = ?, returncode = ?, finished_at = ?,
                elapsed_seconds = ?, error = ?
            WHERE run_id = ?
            """,
            (
                status,
                returncode,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                round(time.perf_counter() - start, 2),
                error,
                run_id,
            ),
        )
        conn.commit()


def _fail_quant_task(run_id: str, error: str, *, returncode: int = 1) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE quant_task_run_log
            SET status = 'failed', returncode = ?, finished_at = ?, error = ?
            WHERE run_id = ?
            """,
            (
                returncode,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                error,
                run_id,
            ),
        )
        conn.commit()


def _validate_mrgc_recalculation_date(trade_date: str) -> None:
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        stock_rows = conn.execute(
            "SELECT COUNT(*) FROM stock_daily_prices WHERE DATE(trade_date) = DATE(?)",
            (trade_date,),
        ).fetchone()[0]
        rps_rows = conn.execute(
            "SELECT COUNT(*) FROM quant_feature_rps WHERE DATE(trade_date) = DATE(?)",
            (trade_date,),
        ).fetchone()[0]
    if not stock_rows or not rps_rows:
        raise RuntimeError(
            f"{trade_date} 上游未就绪: stock_daily_prices={stock_rows}, "
            f"quant_feature_rps={rps_rows}"
        )


def _run_locked_mrgc_task(
    run_id: str,
    command: list[str],
    params: dict[str, Any],
    log_path: Path,
) -> None:
    """Run a manual MRGC rebuild under the same lock as scheduled workflows."""
    WORKFLOW_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WORKFLOW_LOCK_PATH, "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _fail_quant_task(run_id, "Data Core 工作流正在运行，请稍后重试", returncode=75)
            return

        try:
            trade_date = _date_param(params, "trade_date")
            _validate_mrgc_recalculation_date(trade_date)

            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(json.dumps({
                "pid": os.getpid(),
                "run_id": run_id,
                "mode": "manual_mrgc",
                "target_trade_date": trade_date,
                "stage": "mrgc_recalculation",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "heartbeat_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False))
            lock_file.flush()

            from scripts.run_local_data_skill import ensure_database_backup

            backup = ensure_database_backup(trade_date)
            task_params = {
                **params,
                "backup_path": str(backup.path),
                "backup_method": backup.method,
                "backup_reused": backup.reused,
            }
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE quant_task_run_log SET params_json = ? WHERE run_id = ?",
                    (json.dumps(task_params, ensure_ascii=False), run_id),
                )
                conn.commit()

            _run_quant_task(run_id, command, task_params, log_path)
            _strategy_page_cache.clear()
        except Exception as exc:
            _fail_quant_task(run_id, str(exc))
        finally:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.flush()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@router.get("/dates", response_model=ApiResponse)
async def get_quant_dates(limit: int = Query(60, ge=1, le=300)):
    """获取工作台可切换交易日期。"""
    cache_key = _strategy_cache_key("dates", limit)
    cached = _cache_get(_strategy_page_cache, cache_key, _TRADE_DATE_CACHE_TTL)
    if cached is not None:
        return cached

    with _connect() as conn:
        if not _table_exists(conn, "stock_daily_prices"):
            return ApiResponse(data={"dates": [], "latest": None})

        latest_stock_date = _latest_date(conn, "stock_daily_prices")
        if not latest_stock_date:
            return ApiResponse(data={"dates": [], "latest": None})

        if _table_exists(conn, "trading_calendar"):
            rows = conn.execute(
                """
                SELECT trade_date
                FROM trading_calendar
                WHERE is_open = 1
                  AND DATE(trade_date) <= DATE(?)
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (latest_stock_date, limit),
            ).fetchall()
            dates = [str(row["trade_date"])[:10] for row in rows]
        else:
            rows = conn.execute(
                """
                SELECT DATE(trade_date) AS trade_date
                FROM stock_daily_prices
                GROUP BY DATE(trade_date)
                ORDER BY DATE(trade_date) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            dates = [str(row["trade_date"])[:10] for row in rows]

        return _cache_set(
            _strategy_page_cache,
            cache_key,
            ApiResponse(data={"dates": dates, "latest": dates[0] if dates else None}),
        )


@router.get("/dashboard", response_model=ApiResponse)
async def get_quant_dashboard(trade_date: str | None = None, limit: int = Query(20, ge=1, le=100)):
    """工作台总览：市场状态、生命周期分布、买点和交易计划。"""
    limit = _limit(limit, 100)
    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)

        market = _one(conn.execute(
            f"""
            SELECT trade_date, index_code, index_name, close, above_ma20, above_ma50,
                   up_count, down_count, new_high_20_count, new_high_60_count,
                   limit_up_count, limit_down_count, total_amount, amount_ratio_20,
                   rps90_count, market_strength_score, market_state
            FROM market_strength_daily
            WHERE trade_date <= ?
            ORDER BY trade_date DESC,
                     {market_order_case("index_code")}
            LIMIT 1
            """,
            (date,),
        ).fetchone()) if _table_exists(conn, "market_strength_daily") else None

        lifecycle_counts = _rows(conn.execute(
            """
            SELECT lifecycle_state, COUNT(*) AS count
            FROM stock_lifecycle_state
            WHERE trade_date = ?
            GROUP BY lifecycle_state
            ORDER BY lifecycle_state
            """,
            (date,),
        ).fetchall()) if _table_exists(conn, "stock_lifecycle_state") else []

        signal_counts = _rows(conn.execute(
            """
            SELECT signal_type, COUNT(*) AS count, ROUND(AVG(signal_score), 2) AS avg_score
            FROM timing_signals_daily
            WHERE trade_date = ? AND COALESCE(is_invalidated, 0) = 0
            GROUP BY signal_type
            ORDER BY count DESC
            """,
            (date,),
        ).fetchall()) if _table_exists(conn, "timing_signals_daily") else []

        plan_counts = _rows(conn.execute(
            """
            SELECT COALESCE(plan_status, 'active') AS plan_status,
                   COALESCE(risk_status, 'normal') AS risk_status,
                   COUNT(*) AS count
            FROM trade_plans
            WHERE trade_date = ?
            GROUP BY COALESCE(plan_status, 'active'), COALESCE(risk_status, 'normal')
            ORDER BY count DESC
            """,
            (date,),
        ).fetchall()) if _table_exists(conn, "trade_plans") else []

        top_industries = _rows(conn.execute(
            """
            SELECT trade_date, sector_name, industry_strength_score, industry_state,
                   sector_rps_20, sector_rps_50, sector_rps_120,
                   rps90_stock_count, leader_symbol
            FROM industry_strength_daily
            WHERE trade_date = ?
            ORDER BY industry_strength_score DESC
            LIMIT ?
            """,
            (date, limit),
        ).fetchall()) if _table_exists(conn, "industry_strength_daily") else []

        opportunities = _rows(conn.execute(
            """
            SELECT l.symbol, l.name, l.sector_name, l.lifecycle_state, l.state_rank,
                   l.market_state, l.industry_state, l.rps_20, l.rps_50, l.rps_120,
                   l.rps_250,
                   l.structure_score, s.signal_type, s.signal_score,
                   p.recommended_action, p.risk_status, p.current_stop_price,
                   p.latest_close, p.r_multiple, p.reduce_pct
            FROM stock_lifecycle_state l
            LEFT JOIN timing_signals_daily s
              ON l.trade_date = s.trade_date
             AND l.symbol = s.symbol
             AND COALESCE(s.is_invalidated, 0) = 0
            LEFT JOIN trade_plans p
              ON s.signal_id = p.signal_id
            WHERE l.trade_date = ?
              AND l.state_rank >= 4
            ORDER BY COALESCE(s.signal_score, 0) DESC,
                     l.state_rank DESC,
                     l.structure_score DESC
            LIMIT ?
            """,
            (date, limit),
        ).fetchall()) if _table_exists(conn, "stock_lifecycle_state") else []

        # 获取所有指数数据 - 从 market_strength_daily 获取（包含均线数据）
        indices = []
        if _table_exists(conn, "market_strength_daily") and _table_exists(conn, "index_daily"):
            # 一次查询获取所有指数最新数据
            idx_rows = conn.execute(
                """
                SELECT m.index_code, m.index_name, m.close, m.ma_20, m.ma_50, m.ma_120,
                       m.above_ma20, m.above_ma50, m.trade_date
                FROM market_strength_daily m
                INNER JOIN (
                    SELECT index_code, MAX(trade_date) AS max_date
                    FROM market_strength_daily
                    WHERE trade_date <= ?
                    GROUP BY index_code
                ) latest ON m.index_code = latest.index_code AND m.trade_date = latest.max_date
                ORDER BY m.index_code
                """,
                (date,),
            ).fetchall()

            # 批量获取所有指数的前一日收盘价
            all_codes = [r["index_code"] for r in idx_rows]
            prev_close_map = {}
            if all_codes:
                placeholders = ",".join(["?"] * len(all_codes))
                prev_rows = conn.execute(
                    f"""
                    SELECT index_code, close FROM index_daily
                    WHERE trade_date < ? AND index_code IN ({placeholders})
                    ORDER BY index_code, trade_date DESC
                    """,
                    [date] + all_codes,
                ).fetchall()
                for pr in prev_rows:
                    if pr["index_code"] not in prev_close_map:
                        prev_close_map[pr["index_code"]] = pr["close"]

            for row in idx_rows:
                idx_code = row["index_code"]
                pct_chg = 0
                prev_close = prev_close_map.get(idx_code)
                if row["close"] and prev_close:
                    pct_chg = ((row["close"] - prev_close) / prev_close) * 100

                close_val = row["close"]
                above_ma120 = 1 if (row["ma_120"] and close_val >= row["ma_120"]) else 0

                indices.append({
                    "index_code": idx_code,
                    "index_name": row["index_name"],
                    "close": close_val,
                    "ma_20": row["ma_20"],
                    "ma_50": row["ma_50"],
                    "ma_120": row["ma_120"],
                    "above_ma20": row["above_ma20"],
                    "above_ma50": row["above_ma50"],
                    "above_ma120": above_ma120,
                    "pct_chg": round(pct_chg, 2),
                })

        return ApiResponse(
            data={
                "trade_date": date,
                "market_state": market.get("market_state") if market else None,
                "market_strength_score": market.get("market_strength_score") if market else None,
                "market_index_code": market.get("index_code") if market else None,
                "market": market,
                "indices": indices,
                "lifecycle_counts": lifecycle_counts,
                "signal_counts": signal_counts,
                "plan_counts": plan_counts,
                "top_industries": top_industries,
                "opportunities": opportunities,
            }
        )


@router.get("/monitor", response_model=ApiResponse)
async def get_data_monitor():
    """数据采集与计算表健康状态。"""
    with _connect() as conn:
        summaries = []
        for table_name, date_column, key_columns, critical in MONITOR_TABLES:
            summary = _table_summary(conn, table_name, date_column, key_columns)
            summary["critical"] = critical
            summaries.append(summary)

        latest_stock_date = _latest_date(conn, "stock_daily_prices")
        for summary in summaries:
            _apply_lag_status(summary, latest_stock_date)

        pipeline_runs = _rows(conn.execute(
            """
            SELECT run_id, pipeline_name, mode, status, started_at, finished_at,
                   ROUND(elapsed_seconds, 1) AS elapsed_seconds,
                   critical_failed_tasks, failed_tasks, log_path
            FROM pipeline_run_log
            ORDER BY started_at DESC
            LIMIT 10
            """
        ).fetchall()) if _table_exists(conn, "pipeline_run_log") else []
        data_core_quality = _read_data_core_quality(conn)

        return ApiResponse(
            data={
                "database_path": str(DB_PATH),
                "latest_stock_date": latest_stock_date,
                "tables": summaries,
                "pipeline_runs": pipeline_runs,
                "data_core_quality": data_core_quality,
            }
        )


@router.get("/data-management", response_model=ApiResponse)
async def get_data_management(
    include_quality_detail: bool = Query(False),
    include_duplicates: bool = Query(False),
    detail: bool = Query(False),
):
    """数据管理页聚合状态。

    默认轻量响应（前端 Data 页面用）：database / status_counts / tables / task_runs / sync
    detail=true 时返回完整字段（workflow_plan / glossary / sources 等）。
    """
    cache_key = f"dm_{include_quality_detail}_{include_duplicates}_{detail}"
    now = time.time()
    cached = _dm_cache.get(cache_key)
    if cached and (now - cached[0]) < _DM_CACHE_TTL:
        return cached[1]

    _ensure_task_run_table()
    with _connect() as conn:
        monitor_map = {
            table_name: {
                "date_column": date_column,
                "key_columns": key_columns,
                "critical": critical,
            }
            for table_name, date_column, key_columns, critical in MONITOR_TABLES
        }
        all_tables = _table_names(conn)
        table_rows = []
        for table_name in all_tables:
            config = monitor_map.get(table_name, {})
            columns = _columns(conn, table_name)
            date_column = config.get("date_column") or _detect_date_column(columns)
            key_columns = config.get("key_columns") or ()
            summary = _table_summary(
                conn,
                table_name,
                date_column,
                key_columns,
                include_duplicates=include_duplicates,
            )
            summary["critical"] = bool(config.get("critical", False))
            table_rows.append(summary)

        latest_stock_date = _latest_date(conn, "stock_daily_prices")
        for row in table_rows:
            _apply_lag_status(row, latest_stock_date)

        status_counts: dict[str, int] = {}
        for row in table_rows:
            status = str(row.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

        response_data: dict[str, Any] = {
            "database": {
                "path": str(DB_PATH),
                "exists": DB_PATH.exists(),
                "size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
                "latest_stock_date": latest_stock_date,
            },
            "status_counts": status_counts,
            "tables": table_rows,
            "task_runs": _read_task_runs(20),
            "source_coverage": _source_coverage_rows(table_rows),
            "sync": {
                "r2_configured": bool(settings.R2_ACCOUNT_ID and settings.R2_BUCKET_NAME),
                "cloud_db_configured": bool(settings.CLOUD_DATABASE_URL),
                "force_sync_kline": settings.FORCE_SYNC_KLINE,
            },
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        if detail:
            pipeline_runs = _rows(conn.execute(
                """
                SELECT run_id, pipeline_name, mode, status, started_at, finished_at,
                       ROUND(elapsed_seconds, 1) AS elapsed_seconds,
                       critical_failed_tasks, failed_tasks, log_path
                FROM pipeline_run_log
                ORDER BY started_at DESC
                LIMIT 20
                """
            ).fetchall()) if _table_exists(conn, "pipeline_run_log") else []
            if include_quality_detail:
                quality_payload = _read_data_core_quality(conn)
            else:
                quality_payload = _read_data_core_quality_summary(conn)

            response_data.update({
                "workflow_plan": _workflow_plan(),
                "task_catalog": TASK_CATALOG,
                "data_sources": DATA_SOURCES,
                "schedule_bindings": SCHEDULE_BINDINGS,
                "collector_progress": _read_progress_files(),
                "pipeline_runs": pipeline_runs,
                "data_core_quality": quality_payload,
                "log_files": _recent_log_files(),
                "stage_glossary": STAGE_GLOSSARY,
                "system_guide": SYSTEM_GUIDE,
            })

        result = ApiResponse(data=response_data)

    _dm_cache[cache_key] = (time.time(), result)
    return result


@router.get("/data-tasks", response_model=ApiResponse)
async def get_data_tasks(limit: int = Query(20, ge=1, le=100)):
    """数据采集/计算任务目录与最近执行记录。"""
    return ApiResponse(
        data={
            "task_catalog": TASK_CATALOG,
            "data_sources": DATA_SOURCES,
            "schedule_bindings": SCHEDULE_BINDINGS,
            "task_runs": _read_task_runs(limit),
            "stage_glossary": STAGE_GLOSSARY,
            "system_guide": SYSTEM_GUIDE,
        }
    )


@router.post("/data-tasks/run", response_model=ApiResponse)
async def run_data_task(request: QuantTaskRunRequest, background_tasks: BackgroundTasks):
    """手动触发白名单数据任务。"""
    command = _build_task_command(request.task_id, request.params)
    run_id = f"manual_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
    task_title = _task_title(request.task_id)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{run_id}.log"

    _ensure_task_run_table()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO quant_task_run_log (
                run_id, task_id, task_title, status, command, params_json,
                started_at, log_path
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                run_id,
                request.task_id,
                task_title,
                " ".join(command),
                json.dumps(request.params, ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(log_path.relative_to(BACKEND_DIR)),
            ),
        )
        conn.commit()

    task_runner = (
        _run_locked_mrgc_task
        if request.task_id == "mrgc_recalculation"
        else _run_quant_task
    )
    background_tasks.add_task(task_runner, run_id, command, request.params, log_path)
    return ApiResponse(
        data={
            "run_id": run_id,
            "task_id": request.task_id,
            "task_title": task_title,
            "status": "queued",
            "command": " ".join(command),
            "log_path": str(log_path.relative_to(BACKEND_DIR)),
        }
    )


@router.get("/lifecycle", response_model=ApiResponse)
async def get_lifecycle(
    trade_date: str | None = None,
    state: str | None = None,
    sort_by: str | None = Query("state_rank"),
    sort_dir: str | None = Query("desc"),
    limit: int = Query(200, ge=1, le=1000),
):
    """生命周期股票明细。"""
    limit = _limit(limit, 1000)
    cache_key = _strategy_cache_key("lifecycle", trade_date or "", state or "", sort_by or "", sort_dir or "", limit)
    cached = _cache_get(_strategy_page_cache, cache_key, _STRATEGY_PAGE_CACHE_TTL)
    if cached is not None:
        return cached

    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        has_rps = _table_exists(conn, "quant_feature_rps")
        has_watch_meta = _table_has_columns(
            conn,
            "stock_lifecycle_state",
            {"universe_source", "data_status"},
        )
        watch_meta_select = (
            "l.universe_source, l.data_status"
            if has_watch_meta
            else "NULL AS universe_source, NULL AS data_status"
        )
        rps_join = (
            """
            LEFT JOIN (
                SELECT symbol, rps_5, rps_10, rps_20, rps_50, rps_120, rps_250
                FROM quant_feature_rps
                WHERE trade_date >= ?
                  AND trade_date < DATE(?, '+1 day')
            ) r ON r.symbol = l.symbol
            """
            if has_rps
            else ""
        )
        rps_select = ", ".join(
            f"{_stock_rps_expr(period, has_rps)} AS rps_{period}"
            for period in RPS_PERIODS
        )
        sort_column = _lifecycle_sort_column(sort_by)
        direction = _sort_direction(sort_dir)
        params: list[Any] = [date, date] if has_rps else []
        params.append(date)
        where = "WHERE l.trade_date = ?"
        if state:
            where += " AND l.lifecycle_state = ?"
            params.append(state)
        rows = conn.execute(
            f"""
            SELECT l.trade_date, l.symbol, l.name, l.sector_name,
                   l.lifecycle_state, l.state_rank, l.market_state, l.industry_state,
                   {watch_meta_select},
                   {rps_select},
                   l.structure_score, l.structure_state, l.signal_type, l.entry_reason,
                   l.exit_reason, l.is_active
            FROM stock_lifecycle_state l
            {rps_join}
            {where}
            ORDER BY {sort_column} {direction},
                     l.state_rank DESC,
                     l.structure_score DESC,
                     rps_20 DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return _cache_set(
            _strategy_page_cache,
            cache_key,
            ApiResponse(data={"trade_date": date, "rows": _rows(rows)}),
        )


@router.get("/alpha-opportunities", response_model=ApiResponse)
async def get_alpha_opportunities(
    trade_date: str | None = None,
    rps_period: int = Query(20),
    state: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    """Alpha 机会池：真实生命周期 S4/S5 机会，按选定 RPS 排序。"""
    limit = _limit(limit, 1000)
    period = _rps_period(rps_period)
    cache_key = _strategy_cache_key("alpha", trade_date or "", period, state or "", limit)
    cached = _cache_get(_strategy_page_cache, cache_key, _STRATEGY_PAGE_CACHE_TTL)
    if cached is not None:
        return cached

    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        has_rps = _table_exists(conn, "quant_feature_rps")
        has_watch_meta = _table_has_columns(
            conn,
            "stock_lifecycle_state",
            {"universe_source", "data_status"},
        )
        has_signals = _table_exists(conn, "timing_signals_daily")
        has_plans = _table_exists(conn, "trade_plans")
        has_prices = _table_has_columns(conn, "stock_daily_prices", {"close", "pct_chg", "amount"})

        ctes = []
        if has_signals:
            ctes.append(
                """
                ranked_signals AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY trade_date, symbol
                               ORDER BY signal_score DESC, signal_id DESC
                           ) AS rn
                    FROM timing_signals_daily
                    WHERE COALESCE(is_invalidated, 0) = 0
                )
                """
            )
        if has_plans:
            ctes.append(
                """
                ranked_plans AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY trade_date, symbol
                               ORDER BY CASE plan_status
                                          WHEN 'exit_pending' THEN 0
                                          WHEN 'reduce' THEN 1
                                          ELSE 2
                                        END,
                                        r_multiple DESC
                           ) AS rn
                    FROM trade_plans
                )
                """
            )

        with_sql = f"WITH {', '.join(ctes)}" if ctes else ""
        rps_join = (
            """
            LEFT JOIN (
                SELECT symbol, rps_5, rps_10, rps_20, rps_50, rps_120, rps_250
                FROM quant_feature_rps
                WHERE trade_date >= ?
                  AND trade_date < DATE(?, '+1 day')
            ) r ON r.symbol = l.symbol
            """
            if has_rps
            else ""
        )
        signal_join = (
            """
            LEFT JOIN ranked_signals s
              ON s.trade_date = l.trade_date
             AND s.symbol = l.symbol
             AND s.rn = 1
            """
            if has_signals
            else ""
        )
        plan_join = (
            """
            LEFT JOIN ranked_plans p
              ON p.trade_date = l.trade_date
             AND p.symbol = l.symbol
             AND p.rn = 1
            """
            if has_plans
            else ""
        )
        price_join = (
            """
            LEFT JOIN stock_daily_prices dp
              ON dp.trade_date = l.trade_date
             AND dp.symbol = l.symbol
            """
            if has_prices
            else ""
        )
        signal_select = (
            "s.signal_id, s.signal_type, s.signal_score, s.entry_price, s.stop_loss_price"
            if has_signals
            else "NULL AS signal_id, NULL AS signal_type, NULL AS signal_score, NULL AS entry_price, NULL AS stop_loss_price"
        )
        plan_select = (
            """
            p.plan_id, p.plan_status, p.recommended_action, p.risk_status,
            p.current_stop_price, p.latest_close, p.r_multiple, p.reduce_pct
            """
            if has_plans
            else """
            NULL AS plan_id, NULL AS plan_status, NULL AS recommended_action,
            NULL AS risk_status, NULL AS current_stop_price, NULL AS latest_close,
            NULL AS r_multiple, NULL AS reduce_pct
            """
        )
        price_select = (
            "dp.close, dp.pct_chg, dp.amount"
            if has_prices
            else "NULL AS close, NULL AS pct_chg, NULL AS amount"
        )
        rps_select = ", ".join(
            f"{_stock_rps_expr(item, has_rps)} AS rps_{item}"
            for item in RPS_PERIODS
        )
        watch_meta_select = (
            "l.universe_source, l.data_status"
            if has_watch_meta
            else "NULL AS universe_source, NULL AS data_status"
        )
        selected_rps = _stock_rps_expr(period, has_rps)
        params: list[Any] = [date, date] if has_rps else []
        params.append(date)
        where = "WHERE l.trade_date = ?"
        if state:
            where += " AND l.lifecycle_state = ?"
            params.append(state)
        else:
            where += " AND l.lifecycle_state IN ('S4', 'S5')"

        rows = conn.execute(
            f"""
            {with_sql}
            SELECT l.trade_date, l.symbol, l.name, l.sector_name,
                   l.lifecycle_state, l.state_rank, l.market_state, l.industry_state,
                   {watch_meta_select},
                   {rps_select},
                   {selected_rps} AS rps_value,
                   'rps_{period}' AS rps_period,
                   l.structure_score, l.structure_state, l.entry_reason, l.exit_reason,
                   {price_select},
                   {signal_select},
                   {plan_select}
            FROM stock_lifecycle_state l
            {rps_join}
            {signal_join}
            {plan_join}
            {price_join}
            {where}
            ORDER BY rps_value DESC,
                     l.state_rank DESC,
                     l.structure_score DESC,
                     l.symbol ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return _cache_set(
            _strategy_page_cache,
            cache_key,
            ApiResponse(
                data={
                    "trade_date": date,
                    "rps_period": f"rps_{period}",
                    "rows": _rows(rows),
                }
            ),
        )


@router.get("/signals", response_model=ApiResponse)
async def get_signals(
    trade_date: str | None = None,
    signal_type: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    """买点信号明细。"""
    limit = _limit(limit, 1000)
    cache_key = _strategy_cache_key("signals", trade_date or "", signal_type or "", limit)
    cached = _cache_get(_strategy_page_cache, cache_key, _STRATEGY_PAGE_CACHE_TTL)
    if cached is not None:
        return cached

    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        has_rps = _table_exists(conn, "quant_feature_rps")
        has_prices = _table_has_columns(conn, "stock_daily_prices", {"close", "pct_chg", "amount"})
        params: list[Any] = [date]
        where = "WHERE s.trade_date = ? AND COALESCE(s.is_invalidated, 0) = 0"
        if signal_type:
            where += " AND s.signal_type = ?"
            params.append(signal_type)
        rps_join = (
            """
            LEFT JOIN quant_feature_rps r
              ON r.trade_date = s.trade_date
             AND r.symbol = s.symbol
            """
            if has_rps
            else ""
        )
        price_join = (
            """
            LEFT JOIN stock_daily_prices p
              ON p.trade_date = s.trade_date
             AND p.symbol = s.symbol
            """
            if has_prices
            else ""
        )
        rps_select = (
            """
            r.rps_5,
            r.rps_10,
            COALESCE(r.rps_20, l.rps_20) AS rps_20,
            COALESCE(r.rps_50, l.rps_50) AS rps_50,
            r.rps_120,
            r.rps_250
            """
            if has_rps
            else """
            NULL AS rps_5,
            NULL AS rps_10,
            l.rps_20,
            l.rps_50,
            l.rps_120,
            l.rps_250
            """
        )
        price_select = (
            "p.close, p.pct_chg, p.amount"
            if has_prices
            else "NULL AS close, NULL AS pct_chg, NULL AS amount"
        )
        rows = conn.execute(
            f"""
            SELECT s.signal_id, s.trade_date, s.symbol, l.name, l.sector_name,
                   s.signal_type, s.signal_score, s.entry_price, s.stop_loss_price,
                   s.stop_loss_pct, s.suggested_position_pct, s.cooling_until,
                   l.lifecycle_state, l.market_state, l.industry_state,
                   {rps_select},
                   {price_select},
                   l.structure_score, s.detail
            FROM timing_signals_daily s
            LEFT JOIN stock_lifecycle_state l
              ON s.trade_date = l.trade_date AND s.symbol = l.symbol
            {rps_join}
            {price_join}
            {where}
            ORDER BY s.signal_score DESC, l.structure_score DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return _cache_set(
            _strategy_page_cache,
            cache_key,
            ApiResponse(data={"trade_date": date, "rows": _rows(rows)}),
        )


@router.get("/trade-plans", response_model=ApiResponse)
async def get_trade_plans(
    trade_date: str | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    """交易计划与动态风控明细。"""
    limit = _limit(limit, 1000)
    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        params: list[Any] = [date]
        where = "WHERE p.trade_date = ?"
        if status:
            where += " AND p.plan_status = ?"
            params.append(status)
        rows = conn.execute(
            f"""
            SELECT p.plan_id, p.signal_id, p.trade_date, p.symbol, l.name, l.sector_name,
                   p.signal_type, p.entry_price, p.initial_stop_price,
                   p.current_stop_price, p.latest_close, p.max_high_since_entry,
                   p.stop_loss_pct, p.r_multiple, p.days_since_signal,
                   p.suggested_position_pct, p.max_position_pct, p.risk_status,
                   p.recommended_action, p.add_allowed, p.reduce_pct,
                   p.exit_reason, p.plan_status, p.market_risk_action,
                   p.industry_risk_action
            FROM trade_plans p
            LEFT JOIN stock_lifecycle_state l
              ON p.trade_date = l.trade_date AND p.symbol = l.symbol
            {where}
            ORDER BY CASE p.plan_status
                       WHEN 'exit_pending' THEN 0
                       WHEN 'reduce' THEN 1
                       ELSE 2
                     END,
                     p.r_multiple DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return ApiResponse(data={"trade_date": date, "rows": _rows(rows)})


@router.get("/industry-strength", response_model=ApiResponse)
async def get_industry_strength(
    trade_date: str | None = None,
    sort_by: str | None = Query("industry_strength_score"),
    sort_dir: str | None = Query("desc"),
    limit: int = Query(100, ge=1, le=300),
):
    """行业强度排行。"""
    limit = _limit(limit, 300)
    sort_by = getattr(sort_by, "default", sort_by)
    sort_dir = getattr(sort_dir, "default", sort_dir)
    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        sector_rps_table = (
            "quant_feature_sector_rps_v2"
            if _table_exists(conn, "quant_feature_sector_rps_v2")
            else "quant_feature_sector_rps"
        )
        has_sector_rps = _table_exists(conn, sector_rps_table)
        rps_join = (
            """
            LEFT JOIN (
                SELECT sector_name, rps_5, rps_10, rps_20, rps_50, rps_120, rps_250
                FROM {sector_rps_table}
                WHERE trade_date >= ?
                  AND trade_date < DATE(?, '+1 day')
            ) r ON r.sector_name = i.sector_name
            """
            .format(sector_rps_table=sector_rps_table)
            if has_sector_rps
            else ""
        )
        if has_sector_rps:
            rps_select = """
                   r.rps_5 AS sector_rps_5,
                   r.rps_10 AS sector_rps_10,
                   COALESCE(r.rps_20, i.sector_rps_20) AS sector_rps_20,
                   COALESCE(r.rps_50, i.sector_rps_50) AS sector_rps_50,
                   COALESCE(r.rps_120, i.sector_rps_120) AS sector_rps_120,
                   r.rps_250 AS sector_rps_250
            """.format(sector_rps_table=sector_rps_table)
        else:
            rps_select = """
                   NULL AS sector_rps_5,
                   NULL AS sector_rps_10,
                   i.sector_rps_20 AS sector_rps_20,
                   i.sector_rps_50 AS sector_rps_50,
                   i.sector_rps_120 AS sector_rps_120,
                   NULL AS sector_rps_250
            """
        sort_column = _industry_sort_column(sort_by)
        direction = _sort_direction(sort_dir)
        params: list[Any] = [date, date] if has_sector_rps else []
        params.extend([date, limit])
        rows = conn.execute(
            f"""
            SELECT i.trade_date, i.sector_code, i.sector_name, i.sector_type,
                   i.return_5d, i.return_20d, i.return_50d, i.return_120d,
                   {rps_select},
                   i.amount, i.amount_ratio_20,
                   i.rps90_stock_count, i.rps90_stock_ratio, i.new_high_stock_count,
                   i.new_high_stock_ratio, i.leader_symbol, i.leader_strength_score,
                   i.industry_strength_score, i.industry_state
            FROM industry_strength_daily i
            {rps_join}
            WHERE i.trade_date = ?
            ORDER BY {sort_column} {direction},
                     i.industry_strength_score DESC,
                     i.sector_name ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return ApiResponse(data={"trade_date": date, "rows": _rows(rows)})


@router.get("/sector-rps-heatmap", response_model=ApiResponse)
async def get_sector_rps_heatmap(
    trade_date: str | None = None,
    rps_period: int = Query(20),
    days: int = Query(20, ge=1, le=60),
    sectors: int = Query(100, ge=1, le=100),
    universe: str = Query("ranked", description="ranked=按当前RPS取Top板块；common=固定常见一级行业"),
):
    """行业 RPS 热图：默认按当前 RPS 从全量行业口径取 Top 板块，日期按最新在左返回。"""
    period = _rps_period(rps_period)
    universe = str(universe or "ranked").lower()
    if universe not in {"ranked", "common"}:
        raise HTTPException(status_code=400, detail=f"不支持的热图行业口径: {universe}")

    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        sector_rps_table = (
            "quant_feature_sector_rps_v2"
            if _table_exists(conn, "quant_feature_sector_rps_v2")
            else "quant_feature_sector_rps"
        )
        if not _table_exists(conn, sector_rps_table):
            return ApiResponse(
                data={
                    "trade_date": date,
                    "period": period,
                    "dates": [],
                    "sectors": [],
                }
            )

        date_rows = conn.execute(
            f"""
            SELECT DATE(trade_date) AS trade_date
            FROM {sector_rps_table}
            WHERE DATE(trade_date) <= DATE(?)
            GROUP BY DATE(trade_date)
            ORDER BY DATE(trade_date) DESC
            LIMIT ?
            """,
            (date, days),
        ).fetchall()
        dates = [str(row["trade_date"]) for row in date_rows]
        if not dates:
            return ApiResponse(data={"trade_date": date, "period": period, "dates": [], "sectors": []})

        if universe == "common":
            common_values = ", ".join("(?, ?)" for _ in COMMON_INDUSTRY_SECTORS)
            common_params: list[Any] = []
            for index, sector_name in enumerate(COMMON_INDUSTRY_SECTORS, 1):
                common_params.extend([sector_name, index])

            sector_rows = conn.execute(
                f"""
                WITH common_sector(sector_name, sort_order) AS (
                    VALUES {common_values}
                ),
                stock_counts AS (
                    SELECT sector_name, COUNT(DISTINCT symbol) AS stock_count
                    FROM stock_sector_map
                    WHERE sector_type = 'Industry'
                      AND sector_name IS NOT NULL
                      AND TRIM(sector_name) <> ''
                    GROUP BY sector_name
                ),
                latest_strength AS (
                    SELECT sector_name, industry_strength_score, industry_state, return_20d,
                           amount, rps90_stock_count, leader_symbol
                    FROM industry_strength_daily
                    WHERE DATE(trade_date) = DATE(?)
                ),
                latest_rps AS (
                    SELECT sector_name, rps_{period} AS latest_rps
                    FROM {sector_rps_table}
                    WHERE DATE(trade_date) = DATE(?)
                )
                SELECT c.sector_name,
                       c.sort_order,
                       COALESCE(sc.stock_count, 0) AS stock_count,
                       ROUND(lr.latest_rps, 2) AS latest_rps,
                       ROUND(ls.industry_strength_score, 2) AS industry_strength_score,
                       ls.industry_state,
                       ROUND(ls.return_20d, 2) AS return_20d,
                       ls.amount,
                       ls.rps90_stock_count,
                       ls.leader_symbol
                FROM common_sector c
                LEFT JOIN stock_counts sc ON sc.sector_name = c.sector_name
                LEFT JOIN latest_strength ls ON ls.sector_name = c.sector_name
                LEFT JOIN latest_rps lr ON lr.sector_name = c.sector_name
                WHERE lr.sector_name IS NOT NULL
                   OR ls.sector_name IS NOT NULL
                   OR COALESCE(sc.stock_count, 0) > 0
                ORDER BY c.sort_order
                LIMIT ?
                """,
                (*common_params, date, dates[0], sectors),
            ).fetchall()
        else:
            sector_rows = conn.execute(
                f"""
                WITH stock_counts AS (
                    SELECT sector_name, COUNT(DISTINCT symbol) AS stock_count
                    FROM stock_sector_map
                    WHERE sector_type = 'Industry'
                      AND sector_name IS NOT NULL
                      AND TRIM(sector_name) <> ''
                    GROUP BY sector_name
                ),
                latest_strength AS (
                    SELECT sector_name, industry_strength_score, industry_state, return_20d,
                           amount, rps90_stock_count, leader_symbol
                    FROM industry_strength_daily
                    WHERE DATE(trade_date) = DATE(?)
                ),
                latest_rps AS (
                    SELECT sector_name, rps_{period} AS latest_rps
                    FROM {sector_rps_table}
                    WHERE DATE(trade_date) = DATE(?)
                ),
                ranked AS (
                    SELECT lr.sector_name,
                           COALESCE(sc.stock_count, 0) AS stock_count,
                           lr.latest_rps,
                           ls.industry_strength_score,
                           ls.industry_state,
                           ls.return_20d,
                           ls.amount,
                           ls.rps90_stock_count,
                           ls.leader_symbol
                    FROM latest_rps lr
                    LEFT JOIN latest_strength ls ON ls.sector_name = lr.sector_name
                    LEFT JOIN stock_counts sc ON sc.sector_name = lr.sector_name
                    WHERE COALESCE(sc.stock_count, 0) > 0
                )
                SELECT sector_name,
                       ROW_NUMBER() OVER (
                           ORDER BY latest_rps DESC,
                                    industry_strength_score DESC,
                                    sector_name ASC
                       ) AS sort_order,
                       stock_count,
                       ROUND(latest_rps, 2) AS latest_rps,
                       ROUND(industry_strength_score, 2) AS industry_strength_score,
                       industry_state,
                       ROUND(return_20d, 2) AS return_20d,
                       amount,
                       rps90_stock_count,
                       leader_symbol
                FROM ranked
                ORDER BY latest_rps DESC,
                         industry_strength_score DESC,
                         sector_name ASC
                LIMIT ?
                """,
                (date, dates[0], sectors),
            ).fetchall()

        sector_names = [str(row["sector_name"]) for row in sector_rows]
        values_by_sector_date: dict[tuple[str, str], float | None] = {}
        if sector_names:
            sector_placeholders = ", ".join("?" for _ in sector_names)
            date_placeholders = ", ".join("?" for _ in dates)
            value_rows = conn.execute(
                f"""
                SELECT sector_name,
                       DATE(trade_date) AS trade_date,
                       ROUND(rps_{period}, 2) AS rps
                FROM {sector_rps_table}
                WHERE sector_name IN ({sector_placeholders})
                  AND DATE(trade_date) IN ({date_placeholders})
                """,
                (*sector_names, *dates),
            ).fetchall()
            values_by_sector_date = {
                (str(row["sector_name"]), str(row["trade_date"])): row["rps"]
                for row in value_rows
            }

        result = []
        for row in sector_rows:
            item = dict(row)
            sector_name = str(item["sector_name"])
            item["values"] = {
                item_date: values_by_sector_date.get((sector_name, item_date))
                for item_date in dates
            }
            result.append(item)

        return ApiResponse(
            data={
                "trade_date": date,
                "period": period,
                "universe": universe,
                "dates": dates,
                "sectors": result,
            }
        )


def _load_sector_minute_chart(sector: str, period: str, end_date: str | None, limit: int) -> dict:
    """Resolve the canonical Eastmoney board without replacing local daily OHLC."""
    metadata = None
    with closing(_connect()) as conn:
        if _table_exists(conn, "sector_list"):
            columns = _columns(conn, "sector_list")
            if {"sector_name", "sector_code", "sector_type", "source"}.issubset(columns):
                metadata = _one(conn.execute(
                    "SELECT sector_code, sector_type, source FROM sector_list "
                    "WHERE sector_name = ? LIMIT 1", (sector,),
                ).fetchone())
    if (not metadata or not str(metadata.get("sector_code") or "").startswith("BK")
            or "eastmoney" not in str(metadata.get("source") or "").lower()):
        return {
            "period": period, "source": "eastmoney_bk", "adjustment": "none",
            "status": "unavailable", "ready": False, "bars": [],
            "source_count": 0, "cache_hit": False,
            "message": "该板块没有已核验的东财 BK 分钟行情映射，不能从日线生成分钟K线",
        }
    return get_minute_chart(
        "sector", str(metadata["sector_code"]), period, end_date, min(limit, 520),
        sector_type=str(metadata["sector_type"]),
    )


@router.get("/sector-chart", response_model=ApiResponse)
async def get_sector_chart(
    sector_name: str = Query(..., min_length=1),
    trade_date: str | None = None,
    limit: int = Query(260, ge=30, le=800),
    period: str = Query("daily", description="周期: daily, 30min, 120min"),
    mode: str = Query(
        "local",
        description=(
            "local=EvoAlpha等权合成OHLC；composite=兼容等权净值；"
            "vendor=东财BK原始OHLC"
        ),
    ),
):
    """行业板块图表数据。

    Local OHLC is the default visual track.  The canonical RPS input remains
    the separate composite net-value table, while vendor mode stays isolated
    and never falls back to the legacy mixed-source table.
    """
    sector = sector_name.strip()
    if not sector:
        raise HTTPException(status_code=400, detail="行业名称不能为空")
    # Direct unit calls receive FastAPI's Query object when the optional
    # parameter is omitted; HTTP requests pass a plain string.
    raw_mode = getattr(mode, "default", mode)
    chart_mode = str(raw_mode or "local").strip().lower()
    if chart_mode not in {"local", "composite", "vendor"}:
        raise HTTPException(status_code=400, detail=f"不支持的板块图表模式: {mode}")
    raw_limit = getattr(limit, "default", limit)
    limit = max(30, min(int(raw_limit), 800))
    chart_period = str(getattr(period, "default", period)).strip().lower()
    if chart_period not in {"daily", "30min", "120min"}:
        raise HTTPException(status_code=400, detail="period 必须是 daily、30min 或 120min")
    if chart_period != "daily":
        try:
            result = await asyncio.to_thread(
                _load_sector_minute_chart, sector, chart_period, trade_date, limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows = result.pop("bars", [])
        series = _sector_chart_series(rows)
        series.pop("rps_short", None)
        series.pop("rps_long", None)
        return ApiResponse(data={
            **result,
            "sector_name": sector,
            "trade_date": rows[-1]["trade_date"] if rows else trade_date,
            "mode": "vendor",
            "price_kind": "ohlc",
            "count": len(rows),
            "source_label": "东财 BK 原始分钟K线（非本地等权合成）",
            **series,
        })

    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        if chart_mode == "local":
            table_name = "sector_composite_ohlc_daily"
            series_builder = _sector_chart_series
            if not _table_exists(conn, table_name):
                series = series_builder([])
                return ApiResponse(
                    data={
                        "trade_date": date,
                        "sector_name": sector,
                        "mode": chart_mode,
                        "price_kind": "ohlc",
                        "source": "local_equal_weight_ohlc",
                        "ready": False,
                        "status": "not_ready",
                        "message": "EvoAlpha 等权合成K线表尚未初始化",
                        "count": 0,
                        **series,
                    }
                )
            table_columns = _columns(conn, table_name)
            volume_column = "volume" if "volume" in table_columns else "volume_shares"
            amount_column = "amount" if "amount" in table_columns else "amount_cny"
            volume_expr = f"c.{volume_column}" if volume_column in table_columns else "NULL"
            amount_expr = f"c.{amount_column}" if amount_column in table_columns else "NULL"
            quality_expr = "c.quality_flag" if "quality_flag" in table_columns else "'PASS'"
            method_expr = (
                "c.method_version"
                if "method_version" in table_columns
                else "'ew_qfq_ohlc_v1'"
            )
            coverage_expr = "c.coverage_ratio" if "coverage_ratio" in table_columns else "NULL"
            has_sector_rps = _table_exists(conn, "quant_feature_sector_rps_v2")
            rps_table = "quant_feature_sector_rps_v2"
        elif chart_mode == "composite":
            table_name = "sector_composite_daily"
            series_builder = _sector_composite_chart_series
            if not _table_exists(conn, table_name):
                series = series_builder([])
                return ApiResponse(
                    data={
                        "trade_date": date,
                        "sector_name": sector,
                        "mode": chart_mode,
                        "price_kind": "net_value",
                        "source": "local_equal_weight",
                        "ready": False,
                        "status": "not_ready",
                        "message": "等权板块净值表尚未初始化",
                        "count": 0,
                        **series,
                    }
                )
            table_columns = _columns(conn, table_name)
            volume_column = "volume" if "volume" in table_columns else "volume_shares"
            amount_column = "amount" if "amount" in table_columns else "amount_cny"
            volume_expr = f"c.{volume_column}" if volume_column in table_columns else "NULL"
            amount_expr = f"c.{amount_column}" if amount_column in table_columns else "NULL"
            quality_expr = "c.quality_flag" if "quality_flag" in table_columns else "'PASS'"
            method_expr = "c.method_version" if "method_version" in table_columns else "'ew_qfq_v1'"
            coverage_expr = "c.coverage_ratio" if "coverage_ratio" in table_columns else "NULL"
            has_sector_rps = _table_exists(conn, "quant_feature_sector_rps_v2")
            rps_table = "quant_feature_sector_rps_v2"
        else:
            table_name = "sector_vendor_daily"
            series_builder = _sector_chart_series
            if not _table_exists(conn, table_name):
                series = series_builder([])
                return ApiResponse(
                    data={
                        "trade_date": date,
                        "sector_name": sector,
                        "mode": chart_mode,
                        "price_kind": "ohlc",
                        "source": "eastmoney_bk",
                        "ready": False,
                        "status": "unavailable",
                        "message": "东财 BK 原始K线表尚未初始化或当前不可用",
                        "count": 0,
                        **series,
                    }
                )
            table_columns = _columns(conn, table_name)
            volume_column = "volume" if "volume" in table_columns else "volume_shares"
            amount_column = "amount" if "amount" in table_columns else "amount_cny"
            volume_expr = f"c.{volume_column}" if volume_column in table_columns else "NULL"
            amount_expr = f"c.{amount_column}" if amount_column in table_columns else "NULL"
            quality_expr = "c.quality_flag" if "quality_flag" in table_columns else "'PASS'"
            method_expr = "'eastmoney_bk'"
            coverage_expr = "NULL"
            has_sector_rps = _table_exists(conn, "quant_feature_sector_rps_v2")
            rps_table = "quant_feature_sector_rps_v2"

        quality_filter = (
            "AND UPPER(COALESCE(c.quality_flag, 'PASS')) IN ('PASS', 'OK', 'LEGACY_OK')"
        )

        rps_select = (
            "r.rps_5, r.rps_10, r.rps_20, r.rps_50, r.rps_120, r.rps_250"
            if has_sector_rps
            else "NULL AS rps_5, NULL AS rps_10, NULL AS rps_20, "
            "NULL AS rps_50, NULL AS rps_120, NULL AS rps_250"
        )
        rps_join = (
            f"LEFT JOIN {rps_table} r ON r.sector_name = recent.sector_name "
            "AND r.trade_date = recent.trade_date"
            if has_sector_rps
            else ""
        )
        if chart_mode == "composite":
            source_select = "'local_equal_weight' AS source"
            quality_select = "recent.quality_flag"
            method_select = "recent.method_version"
            coverage_select = "recent.coverage_ratio"
            value_select = "recent.net_value AS net_value"
            ohlc_select = "NULL AS open, NULL AS high, NULL AS low, NULL AS close"
        else:
            source_select = (
                "'local_equal_weight_ohlc' AS source"
                if chart_mode == "local"
                else "'eastmoney_bk' AS source"
            )
            quality_select = "recent.quality_flag"
            method_select = "recent.method_version"
            coverage_select = (
                "recent.coverage_ratio"
                if chart_mode == "local"
                else "NULL AS coverage_ratio"
            )
            value_select = ""
            ohlc_select = "c.open, c.high, c.low, c.close"

        rows = conn.execute(
            f"""
                WITH recent AS (
                    SELECT c.sector_name,
                           c.trade_date,
                           {ohlc_select},
                       c.pct_chg,
                       {volume_expr} AS volume,
                       {amount_expr} AS amount,
                       {quality_expr} AS quality_flag,
                       {method_expr} AS method_version,
                           {coverage_expr} AS coverage_ratio,
                           {"c.net_value" if chart_mode == "composite" else "NULL"} AS net_value
                FROM {table_name} c
                WHERE c.sector_name = ?
                  {quality_filter}
                  AND c.trade_date <= ?
                ORDER BY c.trade_date DESC
                LIMIT ?
            )
            SELECT recent.sector_name,
                   recent.trade_date,
                   {value_select + ',' if value_select else ''}
                   recent.open,
                   recent.high,
                   recent.low,
                   recent.close,
                   recent.volume,
                   recent.amount,
                   recent.pct_chg,
                   {quality_select},
                   {method_select},
                   {coverage_select},
                   {source_select},
                   {rps_select}
            FROM recent
            {rps_join}
            ORDER BY recent.trade_date ASC
            """,
            (sector, date, min(limit + 300, 1200)),
        ).fetchall()

        items = _rows(rows)
        series = series_builder(items, tail_limit=limit)
        return ApiResponse(
            data={
                "trade_date": date,
                "sector_name": sector,
                "mode": chart_mode,
                "price_kind": "net_value" if chart_mode == "composite" else "ohlc",
                "source": (
                    "local_equal_weight"
                    if chart_mode == "composite"
                    else "local_equal_weight_ohlc"
                    if chart_mode == "local"
                    else "eastmoney_bk"
                ),
                "method_version": items[-1].get("method_version") if items else None,
                "ready": bool(series["dates"]),
                "status": "ok" if series["dates"] else "empty",
                "count": len(series["dates"]),
                **series,
            }
        )


@router.get("/industry-stocks", response_model=ApiResponse)
async def get_industry_stocks(
    sector_name: str = Query(..., min_length=1),
    trade_date: str | None = None,
    rps_period: int = Query(20),
    sort_by: str | None = None,
    sort_dir: str | None = Query("desc"),
    limit: int = Query(120, ge=1, le=500),
):
    """行业成分股明细，以映射表为主，生命周期/RPS/价格作为增强字段。"""
    period = _rps_period(rps_period)
    sort_column = _industry_stock_sort_column(sort_by, period)
    direction = _sort_direction(sort_dir)
    sector = sector_name.strip()
    if not sector:
        raise HTTPException(status_code=400, detail="行业名称不能为空")

    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        if not _table_exists(conn, "stock_sector_map"):
            return ApiResponse(
                data={"trade_date": date, "sector_name": sector, "total": 0, "rows": []}
            )

        has_stock_info = _table_exists(conn, "stock_info")
        has_prices = _table_exists(conn, "stock_daily_prices")
        has_rps = _table_exists(conn, "quant_feature_rps")
        has_lifecycle = _table_exists(conn, "stock_lifecycle_state")
        price_date = _latest_join_trade_date(conn, "stock_daily_prices", date) if has_prices else None
        rps_date = _latest_join_trade_date(conn, "quant_feature_rps", date) if has_rps else None
        lifecycle_date = (
            _latest_join_trade_date(conn, "stock_lifecycle_state", date) if has_lifecycle else None
        )

        ctes = [
            """
            sector_symbols AS (
                SELECT symbol,
                       MAX(NULLIF(name, '')) AS map_name,
                       MAX(sector_name) AS sector_name
                FROM stock_sector_map
                WHERE sector_name = ?
                  AND sector_type = 'Industry'
                GROUP BY symbol
            )
            """
        ]
        params: list[Any] = [sector]

        with_clause = "WITH " + ",\n".join(ctes)
        stock_info_join = "LEFT JOIN stock_info si ON si.symbol = ss.symbol" if has_stock_info else ""
        price_join = (
            "LEFT JOIN stock_daily_prices p ON p.trade_date = ? AND p.symbol = ss.symbol"
            if has_prices and price_date
            else ""
        )
        if price_join:
            params.append(price_date)
        rps_join = (
            "LEFT JOIN quant_feature_rps r ON r.trade_date = ? AND r.symbol = ss.symbol"
            if has_rps and rps_date
            else ""
        )
        if rps_join:
            params.append(rps_date)
        lifecycle_join = (
            "LEFT JOIN stock_lifecycle_state l ON l.trade_date = ? AND l.symbol = ss.symbol"
            if has_lifecycle and lifecycle_date
            else ""
        )
        if lifecycle_join:
            params.append(lifecycle_date)

        stock_info_name = "si.name" if has_stock_info else "NULL"
        stock_info_industry = "si.industry" if has_stock_info else "NULL"
        stock_name_expr = (
            f"COALESCE(l.name, {stock_info_name}, ss.map_name)"
            if lifecycle_join
            else f"COALESCE({stock_info_name}, ss.map_name)"
        )
        price_select = (
            "p.trade_date AS price_trade_date, p.close, p.pct_chg, p.amount, p.volume"
            if price_join
            else "NULL AS price_trade_date, NULL AS close, NULL AS pct_chg, NULL AS amount, NULL AS volume"
        )
        rps_select = (
            "r.rps_5, r.rps_10, r.rps_20, r.rps_50, r.rps_120, r.rps_250"
            if rps_join
            else """
                 NULL AS rps_5, NULL AS rps_10, NULL AS rps_20,
                 NULL AS rps_50, NULL AS rps_120, NULL AS rps_250
            """
        )
        lifecycle_select = (
            """
            l.trade_date AS lifecycle_trade_date,
            l.sector_name AS lifecycle_sector_name,
            l.lifecycle_state,
            l.state_rank,
            l.market_state,
            l.industry_state,
            l.structure_score,
            l.structure_state,
            l.signal_type,
            l.entry_reason,
            l.exit_reason,
            l.is_active,
            CASE WHEN l.symbol IS NULL THEN 0 ELSE 1 END AS has_lifecycle
            """
            if lifecycle_join
            else """
            NULL AS lifecycle_trade_date,
            NULL AS lifecycle_sector_name,
            NULL AS lifecycle_state,
            NULL AS state_rank,
            NULL AS market_state,
            NULL AS industry_state,
            NULL AS structure_score,
            NULL AS structure_state,
            NULL AS signal_type,
            NULL AS entry_reason,
            NULL AS exit_reason,
            NULL AS is_active,
            0 AS has_lifecycle
            """
        )
        sort_expressions = {
            "symbol": "ss.symbol",
            "name": stock_name_expr,
            "stock_info_industry": stock_info_industry,
            "market_state": "l.market_state" if lifecycle_join else "NULL",
            "industry_state": "l.industry_state" if lifecycle_join else "NULL",
            "signal_type": "l.signal_type" if lifecycle_join else "NULL",
            "close": "p.close" if price_join else "NULL",
            "pct_chg": "p.pct_chg" if price_join else "NULL",
            "amount": "p.amount" if price_join else "NULL",
            "volume": "p.volume" if price_join else "NULL",
            "state_rank": "l.state_rank" if lifecycle_join else "NULL",
            "structure_score": "l.structure_score" if lifecycle_join else "NULL",
            "lifecycle_state": "l.lifecycle_state" if lifecycle_join else "NULL",
        }
        for rps_column in RPS_COLUMNS:
            sort_expressions[rps_column] = f"r.{rps_column}" if rps_join else "NULL"
        sort_expression = sort_expressions[sort_column]
        period_sort_expression = sort_expressions.get(f"rps_{period}", "NULL")
        amount_sort_expression = sort_expressions.get("amount", "NULL")

        rows = conn.execute(
            f"""
            {with_clause}
            SELECT ss.symbol,
                   {stock_name_expr} AS name,
                   ss.sector_name,
                   {stock_info_industry} AS stock_info_industry,
                   {price_select},
                   {rps_select},
                   {lifecycle_select},
                   COUNT(*) OVER() AS total_count
            FROM sector_symbols ss
            {stock_info_join}
            {price_join}
            {rps_join}
            {lifecycle_join}
            ORDER BY {sort_expression} IS NULL ASC,
                     {sort_expression} {direction},
                     {period_sort_expression} IS NULL ASC,
                     {period_sort_expression} DESC,
                     {amount_sort_expression} IS NULL ASC,
                     {amount_sort_expression} DESC,
                     ss.symbol ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

        items = _rows(rows)
        total = int(items[0].get("total_count") or len(items)) if items else 0
        for item in items:
            item.pop("total_count", None)

        return ApiResponse(
            data={
                "trade_date": date,
                "sector_name": sector,
                "period": period,
                "total": total,
                "rows": items,
            }
        )


@router.get("/research/snapshot", response_model=ApiResponse)
async def get_research_snapshot(trade_date: str | None = None, limit: int = Query(200, ge=1, le=1000)):
    """研究池快照。"""
    limit = _limit(limit, 1000)
    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        if not _table_exists(conn, "research_stock_daily_snapshot"):
            return ApiResponse(data={"trade_date": date, "rows": []})
        rows = conn.execute(
            """
            SELECT trade_date, theme_name, node_name, symbol, stock_name,
                   conviction_level, research_status, lifecycle_state,
                   market_state, industry_state, rps_20, rps_50,
                   structure_score, timing_signal_count, latest_signal_type
            FROM research_stock_daily_snapshot
            WHERE trade_date = ?
            ORDER BY timing_signal_count DESC, structure_score DESC
            LIMIT ?
            """,
            (date, limit),
        ).fetchall()
        return ApiResponse(data={"trade_date": date, "rows": _rows(rows)})


@router.get("/review/summary", response_model=ApiResponse)
async def get_review_summary(trade_date: str | None = None):
    """盘后审查摘要：需要行动的交易计划与风险分布。"""
    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        action_items = _rows(conn.execute(
            """
            SELECT p.symbol, l.name, l.sector_name, p.signal_type, p.risk_status,
                   p.recommended_action, p.reduce_pct, p.exit_reason,
                   p.entry_price, p.latest_close, p.current_stop_price,
                   p.r_multiple
            FROM trade_plans p
            LEFT JOIN stock_lifecycle_state l
              ON p.trade_date = l.trade_date AND p.symbol = l.symbol
            WHERE p.trade_date = ?
              AND COALESCE(p.recommended_action, 'hold') <> 'hold'
            ORDER BY p.reduce_pct DESC, p.r_multiple ASC
            LIMIT 50
            """,
            (date,),
        ).fetchall()) if _table_exists(conn, "trade_plans") else []

        risk_distribution = _rows(conn.execute(
            """
            SELECT COALESCE(risk_status, 'normal') AS risk_status, COUNT(*) AS count
            FROM trade_plans
            WHERE trade_date = ?
            GROUP BY COALESCE(risk_status, 'normal')
            ORDER BY count DESC
            """,
            (date,),
        ).fetchall()) if _table_exists(conn, "trade_plans") else []

        return ApiResponse(
            data={
                "trade_date": date,
                "action_items": action_items,
                "risk_distribution": risk_distribution,
            }
        )


@router.get("/backtest/summary", response_model=ApiResponse)
async def get_backtest_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    signal_type: str | None = None,
):
    """买点信号快速前瞻收益统计，作为正式回测前的 MVP 验证。"""
    horizons = (5, 10, 20)
    with _connect() as conn:
        end = end_date or _latest_date(conn, "timing_signals_daily") or _latest_date(conn, "stock_daily_prices")
        start = start_date or (pd.to_datetime(end) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
        if not end:
            return ApiResponse(data={"start_date": start, "end_date": end, "summary": [], "samples": []})

        params: list[Any] = [start, end]
        where = """
            WHERE trade_date BETWEEN ? AND ?
              AND COALESCE(is_invalidated, 0) = 0
        """
        if signal_type:
            where += " AND signal_type = ?"
            params.append(signal_type)
        signals = pd.read_sql_query(
            f"""
            SELECT signal_id, symbol, trade_date, signal_type, entry_price
            FROM timing_signals_daily
            {where}
            ORDER BY trade_date
            """,
            conn,
            params=params,
        )
        if signals.empty:
            return ApiResponse(data={"start_date": start, "end_date": end, "summary": [], "samples": []})

        symbols = signals["symbol"].drop_duplicates().tolist()
        placeholders = ",".join("?" for _ in symbols)
        kline = pd.read_sql_query(
            f"""
            SELECT symbol, trade_date, close
            FROM stock_daily_prices
            WHERE symbol IN ({placeholders})
              AND trade_date > ?
            ORDER BY symbol, trade_date
            """,
            conn,
            params=[*symbols, start],
        )
        for df in (signals, kline):
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="mixed").dt.strftime("%Y-%m-%d")
        signals["entry_price"] = pd.to_numeric(signals["entry_price"], errors="coerce")
        kline["close"] = pd.to_numeric(kline["close"], errors="coerce")
        kline_by_symbol = {symbol: group.sort_values("trade_date") for symbol, group in kline.groupby("symbol")}

        records: list[dict[str, Any]] = []
        for _, signal in signals.iterrows():
            rows = kline_by_symbol.get(signal["symbol"], pd.DataFrame())
            future = rows[rows["trade_date"] > signal["trade_date"]]
            record = signal.to_dict()
            entry_price = float(signal["entry_price"]) if pd.notna(signal["entry_price"]) else 0.0
            for horizon in horizons:
                if entry_price > 0 and len(future) >= horizon:
                    close = float(future.iloc[horizon - 1]["close"])
                    record[f"return_{horizon}d"] = round((close - entry_price) / entry_price * 100, 2)
                else:
                    record[f"return_{horizon}d"] = None
            records.append(record)

        result = pd.DataFrame(records)
        summaries = []
        for group_name, group in result.groupby("signal_type"):
            row: dict[str, Any] = {"signal_type": group_name, "count": int(len(group))}
            for horizon in horizons:
                column = f"return_{horizon}d"
                valid = pd.to_numeric(group[column], errors="coerce").dropna()
                row[f"avg_return_{horizon}d"] = round(float(valid.mean()), 2) if not valid.empty else None
                row[f"win_rate_{horizon}d"] = round(float((valid > 0).mean() * 100), 2) if not valid.empty else None
                row[f"sample_{horizon}d"] = int(valid.count())
            summaries.append(row)

        return ApiResponse(
            data={
                "start_date": start,
                "end_date": end,
                "summary": summaries,
                "samples": records[:50],
            }
        )


@router.get("/ai/brief", response_model=ApiResponse)
async def get_ai_brief(trade_date: str | None = None):
    """本地规则版 AI 摘要占位，后续可接 GLM/OpenAI。"""
    with _connect() as conn:
        date = _resolve_trade_date(conn, trade_date)
        market = _one(conn.execute(
            f"""
            SELECT trade_date, index_code, index_name,
                   market_state, market_strength_score, up_count, down_count,
                   limit_up_count, limit_down_count
            FROM market_strength_daily
            WHERE trade_date <= ?
            ORDER BY trade_date DESC, {market_order_case("index_code")}
            LIMIT 1
            """,
            (date,),
        ).fetchone()) if _table_exists(conn, "market_strength_daily") else None
        signals = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM timing_signals_daily
            WHERE trade_date = ? AND COALESCE(is_invalidated, 0) = 0
            """,
            (date,),
        ).fetchone()["count"] if _table_exists(conn, "timing_signals_daily") else 0
        active_plans = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM trade_plans
            WHERE trade_date = ? AND COALESCE(plan_status, 'active') = 'active'
            """,
            (date,),
        ).fetchone()["count"] if _table_exists(conn, "trade_plans") else 0
        top_industry = _one(conn.execute(
            """
            SELECT sector_name, industry_strength_score, industry_state
            FROM industry_strength_daily
            WHERE trade_date = ?
            ORDER BY industry_strength_score DESC
            LIMIT 1
            """,
            (date,),
        ).fetchone()) if _table_exists(conn, "industry_strength_daily") else None

        bullets = []
        if market:
            bullets.append(
                f"市场状态 {market['market_state']}，强度分 {market['market_strength_score']}，涨跌家数 {market['up_count']}/{market['down_count']}。"
            )
        if top_industry:
            bullets.append(
                f"最强行业为 {top_industry['sector_name']}，行业状态 {top_industry['industry_state']}，强度分 {top_industry['industry_strength_score']}。"
            )
        bullets.append(f"当日有效买点 {signals} 个，活跃交易计划 {active_plans} 个。")
        bullets.append("建议先审查 reduce/exit_pending 计划，再看 S4/S5 与高分行业共振标的。")

        return ApiResponse(
            data={
                "trade_date": date,
                "market_state": market.get("market_state") if market else None,
                "market_strength_score": market.get("market_strength_score") if market else None,
                "market_index_code": market.get("index_code") if market else None,
                "provider": "local_rules",
                "bullets": bullets,
            }
        )


@router.get("/sync/status", response_model=ApiResponse)
async def get_sync_status():
    """数据同步配置状态，不返回任何密钥。"""
    export_tables = [table_name for table_name, _, _, critical in MONITOR_TABLES if critical]
    return ApiResponse(
        data={
            "r2": {
                "configured": bool(settings.R2_ACCOUNT_ID and settings.R2_BUCKET_NAME),
                "bucket": settings.R2_BUCKET_NAME,
                "endpoint_configured": bool(settings.R2_ENDPOINT),
            },
            "cloud_db": {
                "configured": bool(settings.CLOUD_DATABASE_URL),
                "host": settings.CLOUD_DB_HOST or None,
                "database": settings.CLOUD_DB_NAME,
            },
            "local": {
                "database_path": str(DB_PATH),
                "database_exists": DB_PATH.exists(),
                "force_sync_kline": settings.FORCE_SYNC_KLINE,
            },
            "export_tables": export_tables,
            "environment": os.getenv("APP_ENV", settings.APP_ENV),
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@router.get("/strategy/results", response_model=ApiResponse)
async def get_strategy_results(
    trade_date: str | None = None,
    strategy_name: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
):
    """策略预选结果：查 quant_preselect_results 表，按日期和策略名返回。

    可通过 strategy_name 过滤（如 mrgc_v1），不传则返回所有策略。
    结果会 JOIN stock_basic_info 补充股票名称，JOIN stock_lifecycle_state 补充生命周期状态。
    """
    limit = _limit(limit, 2000)
    cache_key = _strategy_cache_key("strategy_results", trade_date or "", strategy_name or "", limit)
    cached = _cache_get(_strategy_page_cache, cache_key, _STRATEGY_PAGE_CACHE_TTL)
    if cached is not None:
        return cached

    with _connect() as conn:
        date = (
            _normalize_explicit_trade_date(trade_date)
            if trade_date
            else _resolve_trade_date(conn, None)
        )

        if not _table_exists(conn, "quant_preselect_results"):
            return ApiResponse(data={"date": date, "rows": [], "total": 0})

        strategy_filter = "AND strategy_name = ?" if strategy_name else ""
        join_params: list = []

        has_lifecycle = _table_exists(conn, "stock_lifecycle_state")
        has_rps = _table_exists(conn, "quant_feature_rps")
        has_prices = _table_has_columns(conn, "stock_daily_prices", {"close", "pct_chg", "amount"})

        lifecycle_join = ""
        rps_join = ""
        price_join = ""
        lifecycle_cols = ""
        rps_cols = ""
        price_cols = ", NULL AS close, NULL AS pct_chg, NULL AS amount"

        if has_lifecycle:
            lifecycle_join = (
                "LEFT JOIN stock_lifecycle_state sls "
                "ON sls.trade_date = ? AND sls.symbol = qpr.symbol"
            )
            join_params.append(date)
            lifecycle_cols = (
                ", sls.market_state, sls.industry_state, sls.lifecycle_state, "
                "sls.structure_score, sls.structure_state"
            )
        if has_rps:
            rps_join = (
                "LEFT JOIN quant_feature_rps rps "
                "ON rps.symbol = qpr.symbol "
                "AND rps.trade_date >= ? "
                "AND rps.trade_date < DATE(?, '+1 day')"
            )
            join_params.extend([date, date])
            if has_lifecycle:
                rps_cols = (
                    ", rps.rps_5, rps.rps_10, "
                    "COALESCE(rps.rps_20, sls.rps_20) AS rps_20, "
                    "COALESCE(rps.rps_50, sls.rps_50) AS rps_50, "
                    "COALESCE(rps.rps_120, sls.rps_120) AS rps_120, "
                    "COALESCE(rps.rps_250, sls.rps_250) AS rps_250"
                )
            else:
                rps_cols = (
                    ", rps.rps_5, rps.rps_10, rps.rps_20, rps.rps_50, "
                    "rps.rps_120, rps.rps_250"
                )
        elif has_lifecycle:
            rps_cols = (
                ", NULL AS rps_5, NULL AS rps_10, sls.rps_20, sls.rps_50, "
                "sls.rps_120, sls.rps_250"
            )
        else:
            rps_cols = (
                ", NULL AS rps_5, NULL AS rps_10, NULL AS rps_20, "
                "NULL AS rps_50, NULL AS rps_120, NULL AS rps_250"
            )
        if has_prices:
            price_join = (
                "LEFT JOIN stock_daily_prices p "
                "ON p.trade_date = ? AND p.symbol = qpr.symbol"
            )
            join_params.append(date)
            price_cols = ", p.close, p.pct_chg, p.amount"

        # stock name from stock_info
        name_join = (
            "LEFT JOIN (SELECT symbol, name, industry FROM stock_info) si "
            "ON si.symbol = qpr.symbol"
        )

        joins = " ".join(filter(None, [name_join, lifecycle_join, rps_join, price_join]))

        sql = f"""
            SELECT qpr.strategy_name, qpr.trade_date, qpr.symbol,
                   COALESCE(si.name, qpr.symbol) AS name,
                   si.industry AS sector_name,
                   qpr.signal_type, qpr.meta_info,
                   qpr.strategy_display_name, qpr.result_type
                   {lifecycle_cols}
                   {rps_cols}
                   {price_cols}
            FROM quant_preselect_results qpr
            {joins}
            WHERE qpr.trade_date = ?
            {strategy_filter}
            ORDER BY qpr.created_at DESC
            LIMIT ?
        """
        params: list = [*join_params, date]
        if strategy_name:
            params.append(strategy_name)
        params.append(limit)

        try:
            cursor = conn.execute(sql, params)
            results = [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            return ApiResponse(code=1, message=f"查询策略结果失败: {exc}", data={"date": date, "rows": [], "total": 0})

        # parse meta_info JSON
        for row in results:
            meta = row.get("meta_info")
            if meta:
                try:
                    row["meta"] = json.loads(meta) if isinstance(meta, str) else meta
                except (json.JSONDecodeError, TypeError):
                    row["meta"] = {}
            else:
                row["meta"] = {}

        run_row = None
        if strategy_name and _table_exists(conn, "strategy_run_log"):
            run_row = conn.execute(
                """
                SELECT status, signal_count, error_message, started_at, finished_at
                FROM strategy_run_log
                WHERE strategy_name = ?
                  AND DATE(trade_date) = DATE(?)
                ORDER BY finished_at DESC
                LIMIT 1
                """,
                (strategy_name, date),
            ).fetchone()

        run_data = dict(run_row) if run_row else None
        run_status = (str(run_data.get("status") or "") or None) if run_data else None
        calculated = run_status == "success" or (run_data is None and bool(results))
        calculation = {
            "calculated": calculated,
            "status": run_status or ("legacy_result" if results else "not_run"),
            "signal_count": (
                int(run_data.get("signal_count") or 0)
                if run_data
                else len(results)
            ),
            "result_count": len(results),
            "needs_recalculation": not calculated,
            "error_message": run_data.get("error_message") if run_data else None,
            "started_at": run_data.get("started_at") if run_data else None,
            "finished_at": run_data.get("finished_at") if run_data else None,
        }

        return _cache_set(
            _strategy_page_cache,
            cache_key,
            ApiResponse(data={
                "date": date,
                "strategy_name": strategy_name,
                "total": len(results),
                "rows": results,
                "calculation": calculation,
            }),
        )


@router.get("/strategy/meta", response_model=ApiResponse)
async def get_strategy_meta():
    """返回可用策略列表（从 quant_preselect_results 表中 distinct）。"""
    with _connect() as conn:
        if not _table_exists(conn, "quant_preselect_results"):
            return ApiResponse(data={"strategies": []})

        rows = conn.execute(
            """
            SELECT strategy_name,
                   strategy_display_name,
                   strategy_description,
                   strategy_logic
            FROM quant_preselect_results
            GROUP BY strategy_name
            ORDER BY strategy_name
            """
        ).fetchall()

        strategies = []
        for row in rows:
            strategies.append({
                "strategy_name": row["strategy_name"],
                "display_name": row["strategy_display_name"] or row["strategy_name"],
                "description": (row["strategy_description"] or "")[:200],
                "logic": (row["strategy_logic"] or "")[:200],
            })

        return ApiResponse(data={"strategies": strategies})
