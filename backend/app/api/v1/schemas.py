"""
API v1 请求和响应模型
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime


# ==================== 通用响应模型 ====================

class ApiResponse(BaseModel):
    """通用API响应"""
    code: int = Field(200, description="状态码")
    message: str = Field("success", description="响应消息")
    data: Optional[dict] = Field(None, description="响应数据")


class PaginatedResponse(BaseModel):
    """分页响应"""
    code: int = Field(200, description="状态码")
    message: str = Field("success", description="响应消息")
    data: dict = Field(..., description="响应数据")
    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页大小")


# ==================== 个股相关模型 ====================

class StockInfo(BaseModel):
    """股票基本信息"""
    symbol: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    industry: Optional[str] = Field(None, description="所属行业")
    market_cap: Optional[float] = Field(None, description="市值（亿元）")
    pe: Optional[float] = Field(None, description="市盈率")
    pb: Optional[float] = Field(None, description="市净率")
    high_52w: Optional[float] = Field(None, description="52周最高价")
    low_52w: Optional[float] = Field(None, description="52周最低价")


class StockKline(BaseModel):
    """股票K线数据"""
    symbol: str = Field(..., description="股票代码")
    trade_date: date = Field(..., description="交易日期")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: float = Field(..., description="成交量")
    amount: float = Field(..., description="成交额")
    pct_chg: float = Field(..., description="涨跌幅(%)")
    turnover_rate: Optional[float] = Field(None, description="换手率(%)")


class StockValuation(BaseModel):
    """股票估值数据"""
    code: str = Field(..., description="股票代码")
    trade_date: date = Field(..., description="交易日期")
    pe_ttm: Optional[float] = Field(None, description="市盈率TTM")
    pb: Optional[float] = Field(None, description="市净率")
    ps_ttm: Optional[float] = Field(None, description="市销率TTM")
    pcf_ttm: Optional[float] = Field(None, description="市现率TTM")
    total_mv: Optional[float] = Field(None, description="总市值")
    circ_mv: Optional[float] = Field(None, description="流通市值")


# ==================== 板块相关模型 ====================

class SectorInfo(BaseModel):
    """板块信息"""
    sector_code: str = Field(..., description="板块代码")
    sector_name: str = Field(..., description="板块名称")
    sector_type: str = Field(..., description="板块类型")


class SectorKline(BaseModel):
    """板块K线数据"""
    sector_code: str = Field(..., description="板块代码")
    trade_date: date = Field(..., description="交易日期")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: float = Field(..., description="成交量")
    amount: float = Field(..., description="成交额")
    pct_chg: float = Field(..., description="涨跌幅(%)")


class SectorConstituents(BaseModel):
    """板块成分股"""
    symbol: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")


# ==================== ETF相关模型 ====================

class ETFInfo(BaseModel):
    """ETF基本信息"""
    etf_code: str = Field(..., description="ETF代码")
    etf_name: str = Field(..., description="ETF名称")
    fund_type: Optional[str] = Field(None, description="基金类型")
    list_date: Optional[date] = Field(None, description="上市日期")


class ETFKline(BaseModel):
    """ETF K线数据"""
    etf_code: str = Field(..., description="ETF代码")
    trade_date: date = Field(..., description="交易日期")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: float = Field(..., description="成交量")
    amount: float = Field(..., description="成交额")
    pct_chg: float = Field(..., description="涨跌幅(%)")


# ==================== 指数相关模型 ====================

class IndexData(BaseModel):
    """指数数据"""
    index_code: str = Field(..., description="指数代码")
    index_name: str = Field(..., description="指数名称")
    trade_date: date = Field(..., description="交易日期")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: float = Field(..., description="成交量")
    pct_chg: float = Field(..., description="涨跌幅(%)")


# ==================== 市场统计相关模型 ====================

class MarketStats(BaseModel):
    """市场统计数据"""
    trade_date: date = Field(..., description="交易日期")
    up_count: int = Field(..., description="上涨股票数")
    down_count: int = Field(..., description="下跌股票数")
    flat_count: Optional[int] = Field(None, description="平盘股票数")
    limit_up_count: Optional[int] = Field(None, description="涨停股票数")
    limit_down_count: Optional[int] = Field(None, description="跌停股票数")
    avg_chg: Optional[float] = Field(None, description="平均涨跌幅(%)")
    total_volume: Optional[int] = Field(None, description="总成交量")
    total_amount: Optional[int] = Field(None, description="总成交额")
    north_money: Optional[float] = Field(None, description="北向资金(亿元)")


# ==================== Alpha机会相关模型 ====================

class AlphaOpportunity(BaseModel):
    """Alpha机会"""
    opportunity_id: str = Field(..., description="机会ID")
    trade_date: date = Field(..., description="交易日期")
    symbol: str = Field(..., description="股票代码")
    opportunity_type: str = Field(..., description="机会类型")
    confidence_score: float = Field(..., description="置信度得分(0-100)")
    reason: str = Field(..., description="机会描述")
    status: str = Field(..., description="状态")


# ==================== 查询参数模型 ====================

class StockQueryParams(BaseModel):
    """股票查询参数"""
    symbol: Optional[str] = Field(None, description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")
    industry: Optional[str] = Field(None, description="所属行业")
    min_market_cap: Optional[float] = Field(None, description="最小市值")
    max_market_cap: Optional[float] = Field(None, description="最大市值")
    min_pe: Optional[float] = Field(None, description="最小市盈率")
    max_pe: Optional[float] = Field(None, description="最大市盈率")


class DateRangeParams(BaseModel):
    """日期范围参数"""
    start_date: Optional[date] = Field(None, description="开始日期")
    end_date: Optional[date] = Field(None, description="结束日期")


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页大小")
