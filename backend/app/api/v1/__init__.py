"""
EvoAlpha OS - API v1 模块

提供所有RESTful API端点
"""
from fastapi import APIRouter, Depends

from app.api.v1.stock import router as stock_router
from app.api.v1.sector import router as sector_router
from app.api.v1.etf import router as etf_router
from app.api.v1.index import router as index_router
from app.api.v1.market import router as market_router
from app.api.v1.alpha import router as alpha_router
from app.api.v1.report import router as report_router
from app.api.v1.quant import router as quant_router
from app.api.v1.watch import router as watch_router
from app.api.v1.portfolio import router as portfolio_router
from app.api.v1.structure_candidate import router as structure_candidate_router
from app.api.v1.chart_signals import router as chart_signals_router
from app.api.v1.research import router as research_router

from app.api.v1.users import enforce_business_access, personal_router, router as users_router

# 创建统一的v1路由器
api_v1_router = APIRouter(prefix="/api/v1")

business_router = APIRouter(dependencies=[Depends(enforce_business_access)])

# 注册所有子路由
business_router.include_router(stock_router)
business_router.include_router(sector_router)
business_router.include_router(etf_router)
business_router.include_router(index_router)
business_router.include_router(market_router)
business_router.include_router(alpha_router)
business_router.include_router(report_router)
business_router.include_router(quant_router)
business_router.include_router(watch_router)
business_router.include_router(portfolio_router)
business_router.include_router(structure_candidate_router)

__all__ = ["api_v1_router"]

business_router.include_router(chart_signals_router)
business_router.include_router(research_router)

api_v1_router.include_router(business_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(personal_router)
