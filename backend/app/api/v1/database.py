"""
API v1 数据库依赖项和工具函数
"""
from sqlalchemy import text
from typing import List, Dict, Any, Optional
from datetime import date, datetime
import pandas as pd

from app.core.database import get_local_engine


engine = get_local_engine()


def execute_query(query: str, params: Optional[Dict] = None) -> pd.DataFrame:
    """
    执行SQL查询并返回DataFrame

    Args:
        query: SQL查询语句
        params: 查询参数字典

    Returns:
        pandas DataFrame
    """
    with engine.begin() as conn:
        if params:
            result = conn.execute(text(query), params)
        else:
            result = conn.execute(text(query))

        # 获取列名
        columns = result.keys()
        # 获取数据
        rows = result.fetchall()
        # 转换为DataFrame
        df = pd.DataFrame(rows, columns=columns)

    return df


def fetch_one(query: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """
    执行查询并返回单条记录

    Args:
        query: SQL查询语句
        params: 查询参数字典

    Returns:
        字典或None
    """
    df = execute_query(query, params)
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def fetch_all(query: str, params: Optional[Dict] = None) -> List[Dict]:
    """
    执行查询并返回所有记录

    Args:
        query: SQL查询语句
        params: 查询参数字典

    Returns:
        字典列表
    """
    df = execute_query(query, params)
    return df.to_dict('records')


def record_exists(table: str, conditions: Dict) -> bool:
    """
    检查记录是否存在

    Args:
        table: 表名
        conditions: 条件字典

    Returns:
        是否存在
    """
    where_clause = " AND ".join([f"{k} = :{k}" for k in conditions.keys()])
    query = f"SELECT 1 FROM {table} WHERE {where_clause} LIMIT 1"

    with engine.begin() as conn:
        result = conn.execute(text(query), conditions).fetchone()

    return result is not None


def get_date_range(table: str, date_column: str = "trade_date") -> Dict[str, Optional[date]]:
    """
    获取表的日期范围

    Args:
        table: 表名
        date_column: 日期列名

    Returns:
        {"min_date": date, "max_date": date}
    """
    query = f"""
        SELECT
            MIN({date_column}) as min_date,
            MAX({date_column}) as max_date
        FROM {table}
    """

    result = fetch_one(query)

    if result and result['min_date']:
        return {
            "min_date": result['min_date'],
            "max_date": result['max_date']
        }
    return {"min_date": None, "max_date": None}


def paginate(query: str, page: int = 1, page_size: int = 20,
             params: Optional[Dict] = None) -> Dict[str, Any]:
    """
    分页查询

    Args:
        query: SQL查询语句
        page: 页码（从1开始）
        page_size: 每页大小
        params: 查询参数字典

    Returns:
        {"data": List, "total": int, "page": int, "page_size": int}
    """
    # 获取总数
    count_query = f"SELECT COUNT(*) as total FROM ({query}) as subq"
    total = fetch_one(count_query, params)['total']

    # 添加分页
    offset = (page - 1) * page_size
    paginated_query = f"{query} LIMIT {page_size} OFFSET {offset}"

    data = fetch_all(paginated_query, params)

    return {
        "data": data,
        "total": total,
        "page": page,
        "page_size": page_size
    }


def format_date(dt: Any) -> Optional[str]:
    """
    格式化日期为字符串

    Args:
        dt: 日期对象

    Returns:
        YYYY-MM-DD格式的字符串
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.strftime('%Y-%m-%d')


def format_datetime(dt: Any) -> Optional[str]:
    """
    格式化日期时间为字符串

    Args:
        dt: 日期时间对象

    Returns:
        YYYY-MM-DD HH:MM:SS格式的字符串
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def validate_date(date_str: str) -> Optional[date]:
    """
    验证并转换日期字符串

    Args:
        date_str: 日期字符串 (YYYY-MM-DD)

    Returns:
        date对象或None
    """
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None
