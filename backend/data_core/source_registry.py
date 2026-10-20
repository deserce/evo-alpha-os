"""Data Core source and table registry.

This module is intentionally declarative: production code can ask what is
allowed, while reports can explain why a source or table is classified that way.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


MODULE_STOCK_DAILY = "stock_daily"
MODULE_STOCK_VALUATION = "stock_valuation"
MODULE_STOCK_BASIC = "stock_basic"
MODULE_MARKET_SIDE = "market_side"
MODULE_RESEARCH = "research"

ROLE_OFFICIAL = "official"
ROLE_SUPPLEMENTAL = "supplemental"
ROLE_DIAGNOSTIC = "diagnostic"
ROLE_LEGACY = "legacy"

TABLE_ROLE_CORE = "core"
TABLE_ROLE_DERIVED = "derived"
TABLE_ROLE_SUPPORT = "support"
TABLE_ROLE_LEGACY = "legacy"


@dataclass(frozen=True)
class SourceDefinition:
    id: str
    name: str
    module: str
    role: str
    allowed_official_write: bool
    blocks_downstream: bool
    configured: bool
    speed: str
    stability: str
    description: str
    applies_to: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TableDefinition:
    name: str
    display_name: str
    module: str
    role: str
    date_column: str | None = None
    key_columns: tuple[str, ...] = ()
    p0_quality_gate: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        item = asdict(self)
        item["key_columns"] = list(self.key_columns)
        return item


SOURCE_REGISTRY: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        id="tdx_mootdx_qfq",
        name="TDX mootdx 前复权",
        module=MODULE_STOCK_DAILY,
        role=ROLE_OFFICIAL,
        allowed_official_write=True,
        blocks_downstream=True,
        configured=True,
        speed="fast",
        stability="high",
        description="正式股票日K主源，标准市场使用 mootdx/pytdx 协议并本地计算 qfq。",
        applies_to="SH/SZ 股票日K、增量更新、区间补齐",
    ),
    SourceDefinition(
        id="tdx_standard_bj",
        name="TDX 标准行情北交所",
        module=MODULE_STOCK_DAILY,
        role=ROLE_OFFICIAL,
        allowed_official_write=True,
        blocks_downstream=True,
        configured=True,
        speed="fast",
        stability="medium",
        description="正式北交所日K源，使用标准 TDX market=2；BSE 暂按未复权写入。",
        applies_to="BSE 股票日K、增量更新、区间补齐",
    ),
    SourceDefinition(
        id="tdx_exhq",
        name="TDX 扩展行情",
        module=MODULE_STOCK_DAILY,
        role=ROLE_DIAGNOSTIC,
        allowed_official_write=False,
        blocks_downstream=False,
        configured=True,
        speed="unknown",
        stability="unknown",
        description="扩展行情诊断源；不作为 BSE 正式日K写入链路。",
        applies_to="diagnostic only",
    ),
    SourceDefinition(
        id="eastmoney_spot_em",
        name="东方财富估值快照",
        module=MODULE_STOCK_VALUATION,
        role=ROLE_SUPPLEMENTAL,
        allowed_official_write=True,
        blocks_downstream=False,
        configured=True,
        speed="medium",
        stability="medium",
        description="估值、市值、换手等非行情阻断字段来源。",
        applies_to="stock_valuation_daily",
    ),
    SourceDefinition(
        id="local_kline_derived",
        name="本地行情推导估值",
        module=MODULE_STOCK_VALUATION,
        role=ROLE_SUPPLEMENTAL,
        allowed_official_write=True,
        blocks_downstream=False,
        configured=True,
        speed="fast",
        stability="high",
        description="在外部估值源不可用时，由本地行情和换手率推导部分市值字段。",
        applies_to="stock_valuation_daily fallback",
    ),
    SourceDefinition(
        id="sina_spot",
        name="新浪估值/快照",
        module=MODULE_STOCK_VALUATION,
        role=ROLE_LEGACY,
        allowed_official_write=True,
        blocks_downstream=False,
        configured=True,
        speed="medium",
        stability="low",
        description="仅保留为非阻断估值兼容源，不允许写正式日K。",
        applies_to="stock_valuation_daily legacy fallback",
    ),
    SourceDefinition(
        id="multi_source_auto",
        name="多源自动 K线",
        module=MODULE_STOCK_DAILY,
        role=ROLE_DIAGNOSTIC,
        allowed_official_write=False,
        blocks_downstream=False,
        configured=True,
        speed="medium",
        stability="medium",
        description="历史自动 fallback 链路；只用于诊断，不写正式 stock_daily_prices。",
        applies_to="diagnostic only",
    ),
    SourceDefinition(
        id="akshare_legacy",
        name="AKShare 旧 K线",
        module=MODULE_STOCK_DAILY,
        role=ROLE_LEGACY,
        allowed_official_write=False,
        blocks_downstream=False,
        configured=True,
        speed="slow",
        stability="medium",
        description="旧 HTTP K线链路；不允许作为正式日K写入源。",
        applies_to="legacy diagnostic only",
    ),
    SourceDefinition(
        id="local_sector_composite",
        name="EvoAlpha 本地等权板块净值",
        module=MODULE_MARKET_SIDE,
        role=ROLE_SUPPLEMENTAL,
        allowed_official_write=True,
        blocks_downstream=True,
        configured=True,
        speed="fast",
        stability="high",
        description="由本地个股复权日K和日期化成分快照确定性计算的板块净值主轨。",
        applies_to="sector_composite_daily、行业 RPS",
    ),
    SourceDefinition(
        id="eastmoney_bk_vendor",
        name="东方财富 BK 板块原始行情",
        module=MODULE_MARKET_SIDE,
        role=ROLE_DIAGNOSTIC,
        allowed_official_write=False,
        blocks_downstream=False,
        configured=True,
        speed="slow",
        stability="low",
        description="东财 BK 真实 OHLC 参考轨；限流、熔断，失败不阻断本地板块净值。",
        applies_to="sector_vendor_daily、蜡烛/美国线/KDJ参考",
    ),
)


TABLE_REGISTRY: tuple[TableDefinition, ...] = (
    TableDefinition(
        name="stock_info",
        display_name="股票基础池",
        module=MODULE_STOCK_BASIC,
        role=TABLE_ROLE_CORE,
        key_columns=("symbol",),
        p0_quality_gate=True,
        description="全市场股票基础池，作为行情完整性校验的 expected universe。",
    ),
    TableDefinition(
        name="stock_daily_prices",
        display_name="股票日K主表",
        module=MODULE_STOCK_DAILY,
        role=TABLE_ROLE_CORE,
        date_column="trade_date",
        key_columns=("symbol", "trade_date"),
        p0_quality_gate=True,
        description="正式日K兼容主表，下游 RPS 和前端继续读取该表。",
    ),
    TableDefinition(
        name="stock_valuation_daily",
        display_name="估值快照",
        module=MODULE_STOCK_VALUATION,
        role=TABLE_ROLE_CORE,
        date_column="trade_date",
        key_columns=("code", "trade_date"),
        p0_quality_gate=False,
        description="估值、市值、换手等补充字段；缺失不阻断股票日K/RPS。",
    ),
    TableDefinition(
        name="trading_calendar",
        display_name="交易日历",
        module=MODULE_STOCK_DAILY,
        role=TABLE_ROLE_CORE,
        date_column="trade_date",
        key_columns=("trade_date", "market"),
        p0_quality_gate=True,
        description="由正式行情历史生成的本地交易日历。",
    ),
    TableDefinition(
        name="data_source_log",
        display_name="数据源日志",
        module="data_core",
        role=TABLE_ROLE_CORE,
        date_column="trade_date",
        key_columns=("run_id", "source", "api_name", "market"),
        p0_quality_gate=False,
        description="记录源/API/市场级成功失败和错误原因。",
    ),
    TableDefinition(
        name="data_quality_report",
        display_name="数据质量报告",
        module="data_core",
        role=TABLE_ROLE_CORE,
        date_column="trade_date",
        key_columns=("run_id", "trade_date", "module", "market"),
        p0_quality_gate=True,
        description="记录市场级质量门，决定是否允许 RPS。",
    ),
    TableDefinition(
        name="quant_feature_rps",
        display_name="个股 RPS",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("symbol", "trade_date"),
        description="由 stock_daily_prices 派生，不是数据源主表。",
    ),
    TableDefinition(
        name="quant_stock_pool",
        display_name="核心股票池",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="add_date",
        key_columns=("pool_name", "symbol", "add_date"),
        description="策略侧结果表。",
    ),
    TableDefinition(
        name="index_daily",
        display_name="指数日线",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        date_column="trade_date",
        key_columns=("index_code", "trade_date"),
        description="市场侧指数行情，不参与股票 P0 质量门。",
    ),
    TableDefinition(
        name="sector_daily_prices",
        display_name="行业/板块日线",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        date_column="trade_date",
        key_columns=("sector_name", "trade_date"),
        description="历史混合来源兼容表；只读诊断/回滚，不再作为板块净值或 RPS 输入。",
    ),
    TableDefinition(
        name="sector_composite_daily",
        display_name="EvoAlpha 等权板块净值",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_CORE,
        date_column="trade_date",
        key_columns=("sector_name", "trade_date"),
        p0_quality_gate=True,
        description="由本地个股复权日K和版本化成分快照生成；行业 RPS 的稳定输入。",
    ),
    TableDefinition(
        name="sector_vendor_daily",
        display_name="东财 BK 原始板块K线",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        date_column="trade_date",
        key_columns=("sector_name", "trade_date"),
        p0_quality_gate=False,
        description="东财 BK 真实 OHLC 参考轨；不可作为净值/RPS兜底。",
    ),
    TableDefinition(
        name="etf_daily_prices",
        display_name="ETF 日线",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        date_column="trade_date",
        key_columns=("symbol", "trade_date"),
        description="ETF RPS 输入，不阻断股票日K质量门。",
    ),
    TableDefinition(
        name="limit_board_trading",
        display_name="涨停/连板明细",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        date_column="trade_date",
        key_columns=("symbol", "trade_date"),
        description="市场情绪支撑表。",
    ),
    TableDefinition(
        name="limit_down_trading",
        display_name="跌停明细",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        date_column="trade_date",
        description="市场情绪支撑表。",
    ),
    TableDefinition(
        name="consecutive_boards_stats",
        display_name="连板统计",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        date_column="trade_date",
        description="市场情绪支撑表。",
    ),
    TableDefinition(
        name="sector_list",
        display_name="板块列表",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        key_columns=("sector_code",),
        description="板块基础资料。",
    ),
    TableDefinition(
        name="stock_sector_map",
        display_name="股票-板块映射",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        key_columns=("symbol", "sector_name"),
        description="主题/板块映射支撑表。",
    ),
    TableDefinition(
        name="etf_info",
        display_name="ETF 基础资料",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_SUPPORT,
        key_columns=("symbol",),
        description="ETF 基础池。",
    ),
    TableDefinition(
        name="stock_finance_summary",
        display_name="财务摘要",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_SUPPORT,
        date_column="report_date",
        key_columns=("code", "report_date"),
        description="低频基本面支撑表。",
    ),
    TableDefinition(
        name="finance_fund_holdings",
        display_name="基金持仓",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_SUPPORT,
        date_column="report_date",
        key_columns=("symbol", "report_date"),
        description="低频机构持仓支撑表。",
    ),
    TableDefinition(
        name="stock_northbound_holdings",
        display_name="北向持仓",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_SUPPORT,
        date_column="hold_date",
        key_columns=("symbol", "hold_date"),
        description="低频资金面支撑表，可降级。",
    ),
    TableDefinition(
        name="macro_indicators",
        display_name="宏观指标",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_SUPPORT,
        date_column="publish_date",
        key_columns=("indicator_code", "publish_date"),
        description="宏观支撑表，不阻断日K/RPS。",
    ),
    TableDefinition(
        name="news_articles",
        display_name="新闻资讯",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_SUPPORT,
        date_column="publish_time",
        key_columns=("url",),
        description="事件和复盘支撑表。",
    ),
    TableDefinition(
        name="news_stock_relation",
        display_name="新闻-股票映射",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_SUPPORT,
        description="新闻关联支撑表。",
    ),
    TableDefinition(
        name="quant_feature_sector_rps",
        display_name="行业 RPS",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("sector_name", "trade_date"),
        description="由行业日线派生。",
    ),
    TableDefinition(
        name="quant_feature_sector_rps_v2",
        display_name="行业 RPS v2",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("sector_name", "trade_date"),
        description="只由 sector_composite_daily 派生；影子运行期间与旧行业 RPS 并存。",
    ),
    TableDefinition(
        name="quant_feature_etf_rps",
        display_name="ETF RPS",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("symbol", "trade_date"),
        description="由 ETF 日线派生。",
    ),
    TableDefinition(
        name="market_strength_daily",
        display_name="市场强度",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("index_code", "trade_date"),
        description="P0 派生指标。",
    ),
    TableDefinition(
        name="industry_strength_daily",
        display_name="行业强度",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("sector_name", "trade_date"),
        description="P0 派生指标。",
    ),
    TableDefinition(
        name="structure_health_daily",
        display_name="结构健康",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("symbol", "trade_date"),
        description="P0 派生指标。",
    ),
    TableDefinition(
        name="stock_lifecycle_state",
        display_name="生命周期状态",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("symbol", "trade_date"),
        description="P0 派生指标。",
    ),
    TableDefinition(
        name="timing_signals_daily",
        display_name="买点信号",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("symbol", "trade_date", "signal_type"),
        description="P1 派生指标。",
    ),
    TableDefinition(
        name="trade_plans",
        display_name="交易计划",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("signal_id",),
        description="P1 派生结果。",
    ),
    TableDefinition(
        name="research_stock_daily_snapshot",
        display_name="研究池快照",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        key_columns=("theme_name", "symbol", "trade_date"),
        description="研究增强输出。",
    ),
    TableDefinition(
        name="quant_preselect_results",
        display_name="预选策略结果",
        module="quant",
        role=TABLE_ROLE_DERIVED,
        date_column="trade_date",
        description="策略预选输出。",
    ),
    TableDefinition(
        name="research_themes",
        display_name="研究主题",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_DERIVED,
        description="主题研究输出。",
    ),
    TableDefinition(
        name="research_theme_nodes",
        display_name="研究主题节点",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_DERIVED,
        description="主题研究输出。",
    ),
    TableDefinition(
        name="research_watchlist",
        display_name="研究观察池",
        module=MODULE_RESEARCH,
        role=TABLE_ROLE_DERIVED,
        description="主题研究输出。",
    ),
    TableDefinition(
        name="pipeline_run_log",
        display_name="生产流水线日志",
        module="ops",
        role=TABLE_ROLE_SUPPORT,
        key_columns=("run_id",),
        description="运行审计日志。",
    ),
    TableDefinition(
        name="quant_task_run_log",
        display_name="手动任务日志",
        module="ops",
        role=TABLE_ROLE_SUPPORT,
        key_columns=("run_id",),
        description="手动任务审计日志。",
    ),
    TableDefinition(
        name="stock_kline_qfq",
        display_name="旧 QFQ K线",
        module=MODULE_STOCK_DAILY,
        role=TABLE_ROLE_LEGACY,
        date_column="trade_date",
        description="兼容/试验表，不参与 Data Core P0 质量门。",
    ),
    TableDefinition(
        name="sector_kline",
        display_name="旧板块 K线",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_LEGACY,
        date_column="trade_date",
        description="旧板块表，不参与股票 P0 质量门。",
    ),
    TableDefinition(
        name="etf_kline",
        display_name="旧 ETF K线",
        module=MODULE_MARKET_SIDE,
        role=TABLE_ROLE_LEGACY,
        date_column="trade_date",
        description="旧 ETF 表，不参与股票 P0 质量门。",
    ),
)


def all_sources() -> tuple[SourceDefinition, ...]:
    return SOURCE_REGISTRY


def source_to_api_dict(source: SourceDefinition) -> dict[str, object]:
    item = source.to_dict()
    item["status"] = "ok" if source.configured else "unconfigured"
    item["is_primary"] = source.role == ROLE_OFFICIAL and source.allowed_official_write
    item["production_enabled"] = source.allowed_official_write
    return item


def api_data_sources(include_diagnostic: bool = True) -> list[dict[str, object]]:
    sources = SOURCE_REGISTRY if include_diagnostic else official_sources_for(MODULE_STOCK_DAILY)
    return [source_to_api_dict(source) for source in sources]


def official_sources_for(module: str) -> tuple[SourceDefinition, ...]:
    return tuple(
        source
        for source in SOURCE_REGISTRY
        if source.module == module
        and source.role == ROLE_OFFICIAL
        and source.allowed_official_write
    )


def get_source(source_id: str) -> SourceDefinition | None:
    normalized = str(source_id).strip()
    for source in SOURCE_REGISTRY:
        if source.id == normalized:
            return source
    return None


def is_official_write_source(source_id: str, module: str) -> bool:
    source = get_source(source_id)
    return bool(
        source
        and source.module == module
        and source.role == ROLE_OFFICIAL
        and source.allowed_official_write
    )


def official_source_ids(module: str) -> tuple[str, ...]:
    return tuple(source.id for source in official_sources_for(module))


def table_definitions(names: Iterable[str] | None = None) -> list[TableDefinition]:
    if names is None:
        return list(TABLE_REGISTRY)
    wanted = set(names)
    return [definition for definition in TABLE_REGISTRY if definition.name in wanted]


def table_definition_map() -> dict[str, TableDefinition]:
    return {definition.name: definition for definition in TABLE_REGISTRY}


def core_table_names() -> tuple[str, ...]:
    return tuple(definition.name for definition in TABLE_REGISTRY if definition.role == TABLE_ROLE_CORE)


def legacy_table_names() -> tuple[str, ...]:
    return tuple(definition.name for definition in TABLE_REGISTRY if definition.role == TABLE_ROLE_LEGACY)
