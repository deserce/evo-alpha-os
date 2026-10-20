"""
mootdx 数据源适配器
mootdx 是对 pytdx 的增强版本，具有更好的服务器池管理和稳定性
"""

import json
import logging
import os
from pathlib import Path
import time
from typing import Optional, List
from datetime import datetime, timedelta
import pandas as pd

from data_core.bse_codes import TDX_BSE_MARKET, to_bse_tdx_symbol

logger = logging.getLogger(__name__)

# 市场代码映射
MARKET_SH = 1  # 上海
MARKET_SZ = 0  # 深圳
MARKET_BJ = TDX_BSE_MARKET  # 北京证券交易所

# K线周期映射
CATEGORY_MAP = {
    "1min": 8,    # 1分钟
    "5min": 0,    # 5分钟
    "15min": 1,   # 15分钟
    "30min": 2,   # 30分钟
    "60min": 3,   # 60分钟
    "daily": 4,   # 日K
    "weekly": 5,  # 周K
    "monthly": 6, # 月K
}


def _normalize_volume_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse mootdx's volume alias without changing the source volume unit."""
    if not df.columns.is_unique:
        raise ValueError("K-line response contains duplicate column names")
    if "vol" not in df.columns:
        return df.copy()
    if "volume" not in df.columns:
        return df.rename(columns={"vol": "volume"})

    # mootdx exposes volume as an alias of vol; conflicting values are unsafe
    # to guess at (in particular, never infer a 100x unit conversion here).
    original = pd.to_numeric(df["vol"], errors="raise")
    alias = pd.to_numeric(df["volume"], errors="raise")
    equal = original.eq(alias) | (original.isna() & alias.isna())
    if not equal.all():
        raise ValueError("K-line vol and volume aliases disagree")
    return df.drop(columns=["vol"])


class MootdxAdapter:
    """
    mootdx 数据源适配器

    优点：
    - 内置服务器池管理
    - 自动选择最快服务器
    - 更好的并发处理
    - 支持多种市场数据
    - 相比 pytdx 更稳定的服务器连接

    缺点：
    - 仍依赖通达信服务器
    - 财务数据不完整
    """

    # 通达信服务器列表（备用）
    SERVERS = [
        # 2026-09-14 实测可返回日线，置于旧节点之前。
        ("59.36.5.11", 7709, "安信"),
        ("117.34.114.14", 7709, "国泰君安1"),
        ("117.34.114.15", 7709, "国泰君安2"),
        ("117.34.114.16", 7709, "国泰君安3"),
        ("117.34.114.17", 7709, "国泰君安4"),
        ("218.75.126.9", 7709, "招商证券上海"),
        ("60.12.136.250", 7709, "中信建投"),
        ("120.76.152.87", 7709, "广发证券"),
        ("218.108.98.244", 7709, "华泰证券"),
        ("114.80.63.12", 7709, "申万宏源"),
        ("114.80.63.33", 7709, "申万宏源2"),
        ("119.147.212.81", 7709, "招商证券深圳"),
        ("101.71.170.10", 7709, "华泰证券2"),
        ("218.108.61.19", 7709, "华泰证券3"),
        ("39.106.218.146", 7709, "中信建投2"),
        ("61.49.50.180", 7709, "国信证券"),
        ("59.173.18.69", 7709, "银河证券"),
    ]
    SERVER_STATE_PATH = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "collection_progress"
        / "tdx_server_state.json"
    )

    def __init__(self, max_retries: int = 3, timeout: int = 10):
        """
        初始化 mootdx 适配器

        Args:
            max_retries: 最大重试次数
            timeout: 连接超时时间(秒)
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.api = None
        self.connected = False
        self.current_server = None
        self.backend = None
        self.success_count = 0
        self.fail_count = 0
        self.last_failure_kind: str | None = None
        self.Quotes = None

        # 尝试导入 mootdx
        try:
            from mootdx.quotes import Quotes
            self.Quotes = Quotes
            self._connect()
        except ImportError:
            logger.warning("⚠️  mootdx 未安装，请运行: pip install mootdx")
            self._connect_pytdx()
        except Exception as e:
            logger.warning(f"⚠️  mootdx 初始化失败: {e}")
            self._connect_pytdx()

    def is_available(self) -> bool:
        """检查适配器是否可用"""
        return self.connected and self.api is not None

    def _connect(self) -> bool:
        """
        优先用 mootdx 连接已验证节点，再降级到自动选服和 pytdx。
        """
        if getattr(self, "Quotes", None) is not None:
            if self._connect_manual():
                return True
            if self._connect_mootdx_auto():
                return True
        return self._connect_pytdx()

    def _connect_mootdx_auto(self) -> bool:
        if getattr(self, "Quotes", None) is None:
            return False

        try:
            # 使用 factory 方法创建客户端
            # market='std' 使用标准市场服务器
            self.api = self.Quotes.factory(
                market='std',
                timeout=self.timeout,
                heartbeat=False,
                auto_retry=False,
                raise_exception=False,
            )

            # 测试连接
            test_data = self.api.bars(symbol='600519', market=MARKET_SH, start=0, offset=1)

            if test_data is not None and not test_data.empty:
                self.connected = True
                self.backend = "mootdx"
                self.current_server = "auto"
                self._save_server_state(
                    host="auto",
                    port=0,
                    name="mootdx_auto",
                    backend="mootdx",
                )
                logger.info("✅ mootdx 已连接 (自动选择服务器)")
                return True
            else:
                logger.warning("mootdx 自动连接失败")
                if hasattr(self.api, "close"):
                    self.api.close()
                self.api = None
                return False

        except Exception as e:
            logger.debug(f"mootdx 自动连接失败: {e}")
            return False

    def _load_server_state(self) -> dict[str, object]:
        try:
            payload = json.loads(self.SERVER_STATE_PATH.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_server_state(
        self,
        *,
        host: str,
        port: int,
        name: str,
        backend: str,
    ) -> None:
        path = self.SERVER_STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "host": host,
            "port": int(port),
            "name": name,
            "backend": backend,
            "last_success_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
        except OSError:
            temp_path.unlink(missing_ok=True)

    def _ordered_servers(self) -> list[tuple[str, int, str]]:
        state = self._load_server_state()
        cached_host = str(state.get("host") or "")
        try:
            cached_port = int(state.get("port") or 0)
        except (TypeError, ValueError):
            cached_port = 0
        cached = [
            server
            for server in self.SERVERS
            if server[0] == cached_host and int(server[1]) == cached_port
        ]
        return [*cached, *(server for server in self.SERVERS if server not in cached)]

    def _connect_pytdx(self) -> bool:
        """mootdx 握手失败时，降级使用 pytdx 同一套通达信服务器。"""
        try:
            from pytdx.hq import TdxHq_API
        except ImportError:
            logger.warning("⚠️  pytdx 未安装，无法作为 mootdx 兜底")
            self.connected = False
            return False

        for host, port, name in self._ordered_servers():
            api = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=False)
            try:
                if not api.connect(host, port, time_out=self.timeout):
                    continue

                test_data = api.get_security_bars(
                    CATEGORY_MAP["daily"], MARKET_SH, "600519", 0, 1
                )
                if test_data:
                    self.api = api
                    self.connected = True
                    self.backend = "pytdx"
                    self.current_server = f"{host}:{port}({name})[pytdx]"
                    self._save_server_state(
                        host=host,
                        port=port,
                        name=name,
                        backend="pytdx",
                    )
                    logger.info(f"✅ pytdx 兜底已连接到 {self.current_server}")
                    return True

                api.disconnect()
            except Exception as e:
                logger.debug(f"pytdx 连接 {host}:{port} 失败: {e}")
                try:
                    api.disconnect()
                except Exception:
                    pass
                continue

        logger.warning("pytdx 无法连接到任何已配置服务器")
        self.connected = False
        self.backend = None
        return False

    def _connect_manual(self) -> bool:
        """手动尝试连接各个服务器"""
        for host, port, name in self._ordered_servers():
            api = None
            try:
                api = self.Quotes.factory(
                    market='std',
                    server=(host, int(port)),
                    timeout=self.timeout,
                    heartbeat=False,
                    auto_retry=False,
                    raise_exception=False,
                )

                test_data = api.bars(symbol='600519', start=0, offset=1)
                if test_data is not None and not test_data.empty:
                    self.api = api
                    self.connected = True
                    self.backend = "mootdx"
                    self.current_server = f"{host}:{port}({name})"
                    self._save_server_state(
                        host=host,
                        port=port,
                        name=name,
                        backend="mootdx",
                    )
                    logger.info(f"✅ mootdx 已连接到 {self.current_server}")
                    return True

            except Exception as e:
                logger.debug(f"连接 {host}:{port} 失败: {e}")
            if api is not None and hasattr(api, "close"):
                try:
                    api.close()
                except Exception:
                    pass

        logger.warning("⚠️  mootdx 无法连接到任何已配置服务器")
        self.api = None
        self.connected = False
        self.backend = None
        return False

    def _ensure_connected(self) -> bool:
        """Ensure the current connection is usable without sending a probe request."""
        if not self.connected or not self.api:
            return self._connect()

        if self._transaction_failed():
            logger.warning("TDX 上一事务失败，丢弃连接后重连")
            self._discard_connection()
            return self._connect()

        return True

    def _transaction_failed(self) -> bool:
        """Check the pytdx transaction behind either adapter backend."""
        api = getattr(self, "api", None)
        if api is None:
            return False
        client = getattr(api, "client", None) if getattr(self, "backend", None) == "mootdx" else api
        return bool(getattr(client, "last_transaction_failed", False))

    def _discard_connection(self) -> None:
        """Close a failed stream before another stock can reuse it."""
        stale_api = getattr(self, "api", None)
        stale_backend = getattr(self, "backend", None)
        self.api = None
        self.connected = False
        self.backend = None
        if stale_api is None:
            return
        try:
            if stale_backend == "pytdx" and hasattr(stale_api, "disconnect"):
                stale_api.disconnect()
            elif hasattr(stale_api, "close"):
                stale_api.close()
        except Exception as exc:
            logger.debug("TDX 失败连接关闭异常: %s", exc)

    def _parse_symbol(self, symbol: str) -> int:
        """
        解析股票代码，返回市场代码

        Args:
            symbol: 股票代码，如 '600519' 或 '000001'

        Returns:
            market: 市场代码 (1-沪市, 0-深市, 2-北交所)
        """
        code = str(symbol).zfill(6)

        # 判断市场
        if code.startswith(('92', '43', '83', '87')):  # 北交所
            return MARKET_BJ
        elif code.startswith(('5', '6', '900', '688')):  # 沪市股票、沪市 ETF
            return MARKET_SH
        elif code.startswith(('0', '15', '16', '2', '3')):    # 深市股票、深市 ETF
            return MARKET_SZ
        else:
            # 默认深市
            return MARKET_SZ

    def _tdx_code(self, symbol: str) -> str:
        """Return the code used in TDX requests."""
        code = str(symbol).zfill(6)
        if self._parse_symbol(code) == MARKET_BJ:
            return to_bse_tdx_symbol(code)
        return code

    def _fetch_bars_data(
        self,
        symbol: str,
        period: str = "daily",
        batch_size: int = 800,
        max_offset: int = 20000,
        max_bars: int | None = None,
    ) -> Optional[pd.DataFrame]:
        """
        分页获取完整 K 线数据。

        mootdx 的分页参数是 start + offset；旧实现一直使用 start=0，
        会重复拉取同一页数据。
        """
        code = str(symbol).zfill(6)
        request_code = self._tdx_code(code)
        market = self._parse_symbol(code)
        frequency = CATEGORY_MAP.get(period, CATEGORY_MAP["daily"])

        all_data = []
        start = 0

        while True:
            request_size = batch_size
            if max_bars is not None:
                remaining = max(0, int(max_bars) - start)
                if remaining <= 0:
                    break
                request_size = min(batch_size, remaining)

            if getattr(self, "backend", "mootdx") == "pytdx":
                raw_data = self.api.get_security_bars(
                    frequency, market, request_code, start, request_size
                )
                data = self.api.to_df(raw_data) if raw_data else None
            elif market == MARKET_BJ:
                raw_data = self.api.client.get_security_bars(
                    frequency, market, request_code, start, request_size
                )
                data = pd.DataFrame(raw_data) if raw_data else None
            else:
                data = self.api.bars(
                    symbol=request_code,
                    market=market,
                    frequency=frequency,
                    start=start,
                    offset=request_size,
                )

            if self._transaction_failed():
                self._discard_connection()
                raise ConnectionError("TDX K-line transaction failed")

            if data is None or data.empty:
                break

            all_data.append(data.copy())

            if len(data) < request_size:
                break

            start += request_size
            if max_bars is not None and start >= int(max_bars):
                break
            if start > max_offset:
                break

        if not all_data:
            return None

        return pd.concat(all_data, ignore_index=True)

    def _fetch_index_bars_data(
        self,
        symbol: str,
        period: str = "daily",
        market: int | None = None,
        batch_size: int = 800,
        max_offset: int = 20000,
        max_bars: int | None = None,
    ) -> Optional[pd.DataFrame]:
        """分页获取指数/板块 K 线数据。"""
        code = str(symbol).zfill(6)
        frequency = CATEGORY_MAP.get(period, CATEGORY_MAP["daily"])
        market_code = self._parse_symbol(code) if market is None else market

        all_data = []
        start = 0

        while True:
            request_size = batch_size
            if max_bars is not None:
                remaining = max(0, int(max_bars) - start)
                if remaining <= 0:
                    break
                request_size = min(batch_size, remaining)

            if getattr(self, "backend", "mootdx") == "pytdx":
                if hasattr(self.api, "get_index_bars"):
                    raw_data = self.api.get_index_bars(
                        frequency, market_code, code, start, request_size
                    )
                else:
                    raw_data = self.api.get_security_bars(
                        frequency, market_code, code, start, request_size
                    )
                data = self.api.to_df(raw_data) if raw_data else None
            else:
                try:
                    data = self.api.index_bars(
                        symbol=code,
                        market=market_code,
                        frequency=frequency,
                        start=start,
                        offset=request_size,
                    )
                except TypeError:
                    data = self.api.index_bars(
                        symbol=code,
                        frequency=frequency,
                        start=start,
                        offset=request_size,
                    )

            if data is None or data.empty:
                break

            all_data.append(data.copy())

            if len(data) < request_size:
                break

            start += request_size
            if max_bars is not None and start >= int(max_bars):
                break
            if start > max_offset:
                break

        if not all_data:
            return None

        return pd.concat(all_data, ignore_index=True)

    def get_xdxr_info(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        获取除权除息信息。
        """
        self.last_failure_kind = None
        if not self._ensure_connected():
            self.last_failure_kind = "connectivity"
            return None

        try:
            code = str(symbol).zfill(6)
            if getattr(self, "backend", "mootdx") == "pytdx":
                market = self._parse_symbol(code)
                raw_data = self.api.get_xdxr_info(market, code)
                df = self.api.to_df(raw_data) if raw_data else None
            else:
                df = self.api.xdxr(symbol=code)

            if self._transaction_failed():
                self.last_failure_kind = "connectivity"
                self._discard_connection()
                return None

            if df is None or df.empty:
                return None

            df = df.copy()
            if {"year", "month", "day"}.issubset(df.columns):
                df["trade_date"] = pd.to_datetime(df[["year", "month", "day"]]).dt.date
            elif "date" in df.columns:
                df["trade_date"] = pd.to_datetime(df["date"]).dt.date
            else:
                return None

            for column in ["category", "fenhong", "peigu", "peigujia", "songzhuangu"]:
                if column not in df.columns:
                    df[column] = 0

            return df

        except Exception as e:
            logger.warning(f"mootdx 获取 {symbol} xdxr 失败: {e}")
            self.last_failure_kind = (
                "connectivity" if isinstance(e, ConnectionError) or self._transaction_failed()
                else "request"
            )
            self._discard_connection()
            return None

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily",
        adjust: str = "qfq",
        max_bars: int | None = None,
    ) -> Optional[pd.DataFrame]:
        """
        获取个股K线数据

        Args:
            symbol: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            period: 周期 (daily 目前支持最好)
            adjust: 复权类型 (支持 none / qfq)

        Returns:
            DataFrame or None
        """
        self.last_failure_kind = None
        if not self._ensure_connected():
            self.fail_count += 1
            self.last_failure_kind = "connectivity"
            return None

        try:
            df = self._fetch_bars_data(symbol=symbol, period=period, max_bars=max_bars)
            if df is None or df.empty:
                self.fail_count += 1
                failed_transaction = self._transaction_failed()
                self.last_failure_kind = "connectivity" if failed_transaction else "no_data"
                if failed_transaction:
                    self._discard_connection()
                return None

            df = _normalize_volume_aliases(df)

            # 重命名列
            column_map = {
                'datetime': 'trade_date',
                'date': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'vol': 'volume',
                'volume': 'volume',
                'amount': 'amount',
            }

            # 检查并重命名存在的列
            for old_name, new_name in column_map.items():
                if old_name in df.columns:
                    df = df.rename(columns={old_name: new_name})

            # 转换日期
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

            # 添加 symbol 列
            df['symbol'] = symbol

            # 按日期排序
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df.drop_duplicates(subset=['trade_date'], keep='last').reset_index(drop=True)

            # 日期过滤
            if start_date:
                start = pd.to_datetime(start_date).date()
                df = df[df['trade_date'] >= start]
            if end_date:
                end = pd.to_datetime(end_date).date()
                df = df[df['trade_date'] <= end]

            # 过滤未来日期
            today = pd.Timestamp.now().date()
            df = df[df['trade_date'] <= today]

            if df.empty:
                self.fail_count += 1
                self.last_failure_kind = "no_data"
                return None

            if adjust == "qfq":
                from data_job.core.mootdx_qfq import apply_mootdx_qfq

                xdxr_df = self.get_xdxr_info(symbol)
                if self.last_failure_kind == "connectivity":
                    self.fail_count += 1
                    return None
                df = apply_mootdx_qfq(df, xdxr_df)
            elif 'close' in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100

            # 选择需要的列
            columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']

            # 如果缺少 turnover_rate 列，添加为 None
            if 'turnover_rate' not in df.columns:
                df['turnover_rate'] = None

            df = df[[c for c in columns if c in df.columns]]

            if df.empty:
                self.fail_count += 1
                self.last_failure_kind = "no_data"
                return None

            self.success_count += 1
            self.last_failure_kind = None
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取 {symbol} K线失败: {e}")
            self.fail_count += 1
            self.last_failure_kind = (
                "connectivity" if isinstance(e, ConnectionError) or self._transaction_failed()
                else "request"
            )
            self._discard_connection()
            return None

    def get_index_kline(
        self,
        symbol: str = "000001",
        start_date: str = None,
        end_date: str = None,
        period: str = "daily",
        market: int | None = None,
        max_bars: int | None = None,
    ) -> Optional[pd.DataFrame]:
        """
        获取指数K线数据

        Args:
            symbol: 指数代码 (如 '000001' - 上证指数)
            start_date: 开始日期
            end_date: 结束日期
            period: 周期
            market: 显式市场代码，避免 000300 等沪市指数被误判为深市
            max_bars: 最多拉取最近 N 根 K 线

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            return None

        try:
            code = str(symbol).zfill(6)
            df = self._fetch_index_bars_data(
                code,
                period=period,
                market=market,
                max_bars=max_bars,
            )
            if df is None or df.empty:
                return None

            df = _normalize_volume_aliases(df)

            # 重命名列
            column_map = {
                'datetime': 'trade_date',
                'date': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'vol': 'volume',
                'volume': 'volume',
                'amount': 'amount',
            }

            for old_name, new_name in column_map.items():
                if old_name in df.columns:
                    df = df.rename(columns={old_name: new_name})

            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            df['symbol'] = symbol

            if 'close' in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100

            df = df.sort_values('trade_date').reset_index(drop=True)

            # 日期过滤
            if start_date:
                start = pd.to_datetime(start_date).date()
                df = df[df['trade_date'] >= start]
            if end_date:
                end = pd.to_datetime(end_date).date()
                df = df[df['trade_date'] <= end]

            columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
            existing_columns = [c for c in columns if c in df.columns]
            df = df[existing_columns]

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取指数 {symbol} K线失败: {e}")
            self.fail_count += 1
            return None

    def get_stock_list(self, market: int = None) -> Optional[pd.DataFrame]:
        """
        获取股票列表

        Args:
            market: 市场代码 (0-深市, 1-沪市, None-全部)

        Returns:
            DataFrame with columns: code, name
        """
        if not self._ensure_connected():
            return None

        try:
            all_stocks = []
            markets = [market] if market is not None else [MARKET_SZ, MARKET_SH]

            for m in markets:
                # 获取股票列表
                data = self.api.stocks(market=m)

                if data is not None and not data.empty:
                    for _, row in data.iterrows():
                        all_stocks.append({
                            'code': row.get('code', ''),
                            'name': row.get('name', ''),
                        })

            if not all_stocks:
                return None

            df = pd.DataFrame(all_stocks)
            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取股票列表失败: {e}")
            self.fail_count += 1
            return None

    def get_realtime_quotes(self, symbols: List[str]) -> Optional[pd.DataFrame]:
        """
        获取实时行情

        Args:
            symbols: 股票代码列表

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            return None

        try:
            # 构造请求数据
            quotes_data = []
            for symbol in symbols:
                market = self._parse_symbol(symbol)
                code = str(symbol).zfill(6)

                data = self.api.quotes(symbol=code, market=market)
                if data is not None and not data.empty:
                    quotes_data.append(data)

            if not quotes_data:
                return None

            df = pd.concat(quotes_data, ignore_index=True)

            # 标准化列名
            df = df.rename(columns={
                'code': 'symbol',
                'price': 'current_price',
                'ask1': 'ask_price1',
                'bid1': 'bid_price1',
            })

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取实时行情失败: {e}")
            self.fail_count += 1
            return None

    def get_success_rate(self) -> float:
        """获取成功率"""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        return self.success_count / total

    def get_etf_list(self) -> Optional[pd.DataFrame]:
        """
        获取ETF列表

        Returns:
            DataFrame with columns: code, name
        """
        if not self._ensure_connected():
            return None

        try:
            # 从沪市获取ETF列表 (市场1, category=8)
            all_etfs = []

            # 沪市ETF
            try:
                data = self.api.stocks(market=1, category=8)
                if data is not None and not data.empty:
                    data['market'] = 'SH'
                    all_etfs.append(data)
            except Exception as e:
                logger.debug(f"获取沪市ETF失败: {e}")

            # 深市ETF
            try:
                data = self.api.stocks(market=0, category=8)
                if data is not None and not data.empty:
                    data['market'] = 'SZ'
                    all_etfs.append(data)
            except Exception as e:
                logger.debug(f"获取深市ETF失败: {e}")

            if not all_etfs:
                return None

            df = pd.concat(all_etfs, ignore_index=True)

            # 重命名列
            df = df.rename(columns={'code': 'symbol', 'name': 'name'})
            df = df[['symbol', 'name']]

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取ETF列表失败: {e}")
            self.fail_count += 1
            return None

    def get_etf_kline(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily",
        max_bars: int | None = None,
    ) -> Optional[pd.DataFrame]:
        """
        获取ETF K线数据

        Args:
            symbol: ETF代码
            start_date: 开始日期
            end_date: 结束日期
            period: 周期

        Returns:
            DataFrame or None
        """
        # ETF 按未复权行情保存，避免每日增量触发全量复权计算。
        return self.get_stock_kline(
            symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust="none",
            max_bars=max_bars,
        )

    def get_sector_list(self) -> Optional[pd.DataFrame]:
        """
        获取板块列表

        Returns:
            DataFrame with columns: sector_name, sector_type
        """
        if not self._ensure_connected():
            return None

        try:
            # 获取分类板块
            all_sectors = []

            # 从mootdx获取板块数据需要使用stocks接口的不同category
            # category=5 是分类板块
            try:
                data = self.api.stocks(market=1, category=5)
                if data is not None and not data.empty:
                    all_sectors.append(data)
            except:
                pass

            if not all_sectors:
                return None

            df = pd.concat(all_sectors, ignore_index=True)

            # 重命名列
            df = df.rename(columns={'code': 'sector_symbol', 'name': 'sector_name', 'category': 'sector_type'})

            # 只需要的列
            df = df[['sector_symbol', 'sector_name', 'sector_type']]

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取板块列表失败: {e}")
            self.fail_count += 1
            return None

    def get_sector_kline(
        self,
        sector_symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily"
    ) -> Optional[pd.DataFrame]:
        """
        获取板块K线数据（通过指数方式）

        Args:
            sector_symbol: 板块代码
            start_date: 开始日期
            end_date: 结束日期
            period: 周期

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            return None

        try:
            code = str(sector_symbol).zfill(6)
            df = self._fetch_index_bars_data(code, period=period)
            if df is None or df.empty:
                self.fail_count += 1
                return None

            df = _normalize_volume_aliases(df)

            # 标准化列名
            df = df.rename(columns={
                'datetime': 'trade_date',
                'date': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'vol': 'volume',
                'volume': 'volume',
                'amount': 'amount',
            })

            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

            df['symbol'] = sector_symbol

            if 'close' in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100

            df = df.sort_values('trade_date').reset_index(drop=True)

            # 日期过滤
            if start_date:
                start = pd.to_datetime(start_date).date()
                df = df[df['trade_date'] >= start]
            if end_date:
                end = pd.to_datetime(end_date).date()
                df = df[df['trade_date'] <= end]

            # 过滤未来日期
            today = pd.Timestamp.now().date()
            df = df[df['trade_date'] <= today]

            columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']
            if 'turnover_rate' not in df.columns:
                df['turnover_rate'] = None

            df = df[[c for c in columns if c in df.columns]]

            if df.empty:
                self.fail_count += 1
                return None

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取板块 {sector_symbol} K线失败: {e}")
            self.fail_count += 1
            return None

    def disconnect(self):
        """断开连接"""
        if getattr(self, "api", None):
            try:
                if getattr(self, "backend", "mootdx") == "pytdx" and hasattr(self.api, "disconnect"):
                    self.api.disconnect()
                elif hasattr(self.api, "close"):
                    self.api.close()
                self.connected = False
                logger.info("mootdx/pytdx 已断开连接")
            except:
                pass

    def __del__(self):
        """析构函数，确保断开连接"""
        self.disconnect()


# 单例模式
_mootdx_instance = None


def get_mootdx_adapter() -> Optional[MootdxAdapter]:
    """获取 mootdx 适配器单例"""
    global _mootdx_instance

    if _mootdx_instance is None:
        _mootdx_instance = MootdxAdapter()

    return _mootdx_instance if _mootdx_instance.is_available() else None


# 使用示例
if __name__ == "__main__":
    # 测试获取K线数据
    mootdx = MootdxAdapter()

    if mootdx.is_available():
        print("✅ mootdx 连接成功")
        print(f"服务器: {mootdx.current_server}")

        # 获取贵州茅台K线
        df = mootdx.get_stock_kline('600519', start_date='2024-01-01')
        if df is not None:
            print(f"获取到 {len(df)} 条K线数据")
            print(df.tail(3))
        else:
            print("获取K线数据失败")

        # 打印统计
        print(f"\n成功率: {mootdx.get_success_rate():.1%}")
    else:
        print("❌ mootdx 连接失败")

    mootdx.disconnect()
