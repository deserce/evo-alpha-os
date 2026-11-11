"""
mootdx 数据源适配器
mootdx 是对 pytdx 的增强版本，具有更好的服务器池管理和稳定性
"""

import json
import logging
import math
import os
import struct
from copy import copy
from pathlib import Path
from threading import RLock
import time
import zlib
from typing import Callable, Optional, List
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
    - 有界服务器候选轮换与共享健康状态
    - 真实行情验证与故障冷却
    - 更好的并发处理
    - 支持多种市场数据
    - 相比 pytdx 更稳定的服务器连接

    缺点：
    - 仍依赖通达信服务器
    - 财务数据不完整
    """

    # 通达信服务器列表（备用）
    SERVERS = [
        # 2026-10-25 verified with SH/SZ/BSE bars through 2026-10-23.
        ("139.9.52.158", 7709, "国泰君安深圳"),
        ("103.251.85.94", 7709, "国泰君安上海"),
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
    MAX_CANDIDATES = 6
    VALIDATION_BUDGET_SECONDS = 30.0
    INITIAL_COOLDOWN_SECONDS = 600
    _health_lock = RLock()
    _health_states: dict[str, dict] = {}
    _instance = None
    _instance_lock = RLock()

    class _DeadlineSocket:
        """Apply one synchronous deadline to every setup/probe socket operation."""

        def __init__(self, sock, timeout: float, deadline: float):
            self.sock = sock
            self.timeout = timeout
            self.deadline: float | None = deadline

        def _prepare(self):
            timeout = self.timeout
            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("TDX validation budget exhausted")
                timeout = min(timeout, remaining)
            self.sock.settimeout(timeout)

        def recv(self, *args, **kwargs):
            self._prepare()
            return self.sock.recv(*args, **kwargs)

        def send(self, *args, **kwargs):
            self._prepare()
            return self.sock.send(*args, **kwargs)

        def sendall(self, *args, **kwargs):
            self._prepare()
            return self.sock.sendall(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.sock, name)

    def __init__(
        self, max_retries: int = 3, timeout: int = 10,
        *, connection_deadline: float | None = None, managed_recovery: bool = False,
    ):
        """
        初始化 mootdx 适配器

        Args:
            max_retries: 兼容旧调用；每个业务最多额外重试一次
            timeout: 连接超时时间(秒)
            connection_deadline: 可选的单次启动总预算截止时间（monotonic）
        """
        if not math.isfinite(float(timeout)) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.max_retries = 1  # Retained for callers; retries are bounded per business operation.
        self.timeout = min(float(timeout), self.VALIDATION_BUDGET_SECONDS)
        self._connection_deadline = connection_deadline
        self._pool_managed = managed_recovery
        self.api = None
        self.connected = False
        self.current_server = None
        self.backend = None
        self.success_count = 0
        self.fail_count = 0
        self.last_failure_kind: str | None = None
        self.Quotes = None
        self._active_server: tuple[str, int, str] | None = None

        # 尝试导入 mootdx
        try:
            from mootdx.quotes import Quotes
            self.Quotes = Quotes
        except ImportError:
            logger.warning("⚠️  mootdx 未安装，请运行: pip install mootdx")
        self._connect()

    def is_available(self) -> bool:
        """检查适配器是否可用"""
        return self.connected and self.api is not None

    def _connect(self) -> bool:
        """One discovery round, shared by both backends; never use mootdx bestip."""
        self._discard_connection()
        self._round_deadline = time.monotonic() + self.VALIDATION_BUDGET_SECONDS
        external = getattr(self, "_connection_deadline", None)
        self._connection_deadline = None
        if external is not None:
            self._round_deadline = min(self._round_deadline, external)
        self._round_attempts = 0
        self._active_server = None
        if getattr(self, "Quotes", None) is not None:
            if self._connect_manual():
                return True
        return self._connect_pytdx()

    def _connect_mootdx_auto(self) -> bool:
        """Compatibility entry point: automatic rotation of explicit candidates only."""
        return self._connect_manual()

    def _load_server_state(self) -> dict[str, object]:
        path = self.SERVER_STATE_PATH
        try:
            if path.stat().st_size > 65536:
                return {}
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            nodes = payload.get("nodes", {})
            allowed = {f"{host}:{port}" for host, port, _ in self.SERVERS}
            payload["nodes"] = {
                key: value for key, value in nodes.items()
                if key in allowed and isinstance(value, dict)
            } if isinstance(nodes, dict) else {}
            return payload
        except (OSError, ValueError, TypeError):
            return {}

    def _health_state(self) -> dict:
        """Called under _health_lock. Keep a bounded shared registry if disk is read-only."""
        key = str(self.SERVER_STATE_PATH)
        read_only = getattr(self, "_read_only_health", False)
        signature = None
        if read_only:
            key += ":minute"
            try:
                stat = self.SERVER_STATE_PATH.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                pass
        states = self._health_states
        if (key not in states or (
                read_only and states[key].get("_source_signature") != signature)):
            local_failures = states.get(key, {}).get("_local_failures", {}) if read_only else {}
            if key not in states and len(states) >= 8:
                states.pop(next(iter(states)))
            states[key] = self._load_server_state()
            if read_only:
                # Observe external daily node recovery without polling the DB;
                # minute-only failures must not alter daily persistence throttles.
                local_failures = {
                    node: health for node, health in local_failures.items()
                    if health["cooldown_until"] > time.time()
                }
                states[key]["_local_failures"] = local_failures
                nodes = states[key].setdefault("nodes", {})
                for node, health in local_failures.items():
                    disk_until = nodes.get(node, {}).get("cooldown_until", 0)
                    if (not isinstance(disk_until, (int, float)) or not math.isfinite(disk_until)
                            or health["cooldown_until"] > disk_until):
                        nodes[node] = health
                states[key]["_source_signature"] = signature
        return states[key]

    def _write_server_state(self, payload: dict) -> None:
        path = self.SERVER_STATE_PATH
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(path)
        except OSError as exc:
            logger.debug("TDX health state write failed: %s", exc)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("TDX temporary health state cleanup failed: %s", exc)

    def _save_server_state(
        self, *, host: str, port: int, name: str, backend: str, persist: bool = True,
    ) -> None:
        """Only a completed, nonempty business request may promote a concrete endpoint."""
        if (host, port) not in {(item[0], item[1]) for item in self.SERVERS}:
            return
        with self._health_lock:
            state = self._health_state()
            previous = (state.get("host"), state.get("port"), state.get("backend"))
            state.update(host=host, port=int(port), name=name, backend=backend)
            # At most one small disk write per minute on a continuously healthy node.
            now = time.time()
            last_write = state.get("success_epoch", 0)
            if previous == (host, port, backend) and isinstance(last_write, (int, float)):
                if now - last_write < 60:
                    return
            state["success_epoch"] = now
            state["last_success_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nodes = state.setdefault("nodes", {})
            health = nodes.get(f"{host}:{port}", {})
            until = health.get("cooldown_until", 0)
            if not isinstance(until, (int, float)) or until <= now:
                nodes.pop(f"{host}:{port}", None)
            if persist:
                self._write_server_state(state)

    def _mark_server_failure(
        self, server: tuple[str, int, str], *, persist: bool = True,
    ) -> None:
        with self._health_lock:
            state = self._health_state()
            nodes = state.get("nodes")
            if not isinstance(nodes, dict):
                nodes = {}
            allowed = {f"{host}:{port}" for host, port, _ in self.SERVERS}
            nodes = {key: value for key, value in nodes.items() if key in allowed}
            key = f"{server[0]}:{server[1]}"
            old = nodes.get(key, {})
            now = time.time()
            until = old.get("cooldown_until", 0) if isinstance(old, dict) else 0
            if isinstance(until, (int, float)) and math.isfinite(until) and until > now:
                return
            failures = old.get("failures", 0) if isinstance(old, dict) else 0
            failures = min(4, max(0, failures) + 1) if isinstance(failures, int) else 1
            nodes[key] = {
                "failures": failures,
                "cooldown_until": now + self.INITIAL_COOLDOWN_SECONDS * 2 ** (failures - 1),
            }
            state["nodes"] = nodes
            if getattr(self, "_read_only_health", False):
                state.setdefault("_local_failures", {})[key] = nodes[key]
            if persist:
                self._write_server_state(state)

    def _ordered_servers(self) -> list[tuple[str, int, str]]:
        with self._health_lock:
            state = self._health_state()
            nodes = state.get("nodes", {})
            nodes = nodes if isinstance(nodes, dict) else {}
            cursor = state.get("cursor", 0)
            cursor = cursor % len(self.SERVERS) if isinstance(cursor, int) and self.SERVERS else 0
            rotated = [*self.SERVERS[cursor:], *self.SERVERS[:cursor]]
            eligible = []
            now = time.time()
            for server in rotated:
                health = nodes.get(f"{server[0]}:{server[1]}", {})
                until = health.get("cooldown_until", 0) if isinstance(health, dict) else 0
                if isinstance(until, (int, float)) and math.isfinite(until) and until > now:
                    continue
                eligible.append(server)

            def failure_rank(server):
                health = nodes.get(f"{server[0]}:{server[1]}", {})
                failures = health.get("failures", 0) if isinstance(health, dict) else 0
                return failures if isinstance(failures, int) else 0

            eligible.sort(key=failure_rank)
            cached = [
                s for s in eligible
                if (s[0], s[1]) == (state.get("host"), state.get("port"))
                and not failure_rank(s)
            ]
            return [*cached, *(s for s in eligible if s not in cached)]

    def _server_is_quarantined(self, server: tuple[str, int, str]) -> bool:
        with self._health_lock:
            nodes = self._health_state().get("nodes", {})
            health = nodes.get(f"{server[0]}:{server[1]}", {})
            until = health.get("cooldown_until", 0) if isinstance(health, dict) else 0
            return isinstance(until, (int, float)) and until > time.time()

    @classmethod
    def read_isolated_minute_bars(
        cls, *, kind: str, symbol: str, market: int, period: str, count: int,
        deadline: float, validate: Callable[[list[dict]], None] | None = None,
    ) -> list[dict]:
        """Read at most two pages/two nodes, without discovery or disk writes.

        Each attempt owns its socket. Minute failures and successful endpoints
        update only the bounded in-process health registry used for selection.
        """
        if (kind not in {"stock", "index"} or period not in {"30min", "60min"}
                or not isinstance(symbol, str) or len(symbol) != 6
                or not symbol.isascii() or not symbol.isdigit()
                or isinstance(market, bool) or market not in {MARKET_SZ, MARKET_SH}
                or isinstance(count, bool) or not isinstance(count, int)
                or not 1 <= count <= 1040
                or isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or not math.isfinite(deadline)
                or (validate is not None and not callable(validate))):
            raise ValueError("invalid isolated minute request")
        deadline = min(deadline, time.monotonic() + 10.0)
        if time.monotonic() >= deadline:
            raise TimeoutError("TDX minute request budget exhausted")

        # Bypass the normal constructor's discovery/probes and pooled streams.
        adapter = cls.__new__(cls)
        adapter.timeout = 2.0
        adapter._read_only_health = True
        with cls._health_lock:
            preferred_backend = adapter._health_state().get("backend")
        backend = "pytdx" if preferred_backend == "pytdx" else "mootdx"
        last_error = None
        for server in adapter._ordered_servers()[:2]:
            if time.monotonic() >= deadline:
                break
            attempt_deadline = min(deadline, time.monotonic() + 5.0)
            api = None
            quarantine = True
            try:
                try:
                    api = adapter._open_api(server, backend, attempt_deadline)
                except ImportError:
                    if backend != "mootdx":
                        raise
                    backend = "pytdx"
                    api = adapter._open_api(server, backend, attempt_deadline)
                client = api.client if backend == "mootdx" else api
                method = client.get_index_bars if kind == "index" else client.get_security_bars
                rows = []
                for offset in range(0, count, 800):
                    if time.monotonic() >= attempt_deadline:
                        raise TimeoutError("TDX minute request budget exhausted")
                    size = min(800, count - offset)
                    page = method(CATEGORY_MAP[period], market, symbol, offset, size)
                    if time.monotonic() >= attempt_deadline:
                        raise TimeoutError("TDX minute request budget exhausted")
                    if getattr(client, "last_transaction_failed", False):
                        raise ConnectionError("TDX minute transaction failed")
                    if not page:
                        quarantine = False
                        raise ConnectionError("TDX minute bars unavailable")
                    if not isinstance(page, list) or len(page) > size:
                        raise ValueError("TDX minute response exceeds page limit")
                    for row in page:
                        if (not isinstance(row, dict)
                                or (row.get("code") is not None and str(row["code"]) != symbol)):
                            raise ValueError("TDX minute response has invalid security code")
                    rows.extend(page)
                    if len(page) < size:
                        break
                if validate is not None:
                    validate(rows)
                if time.monotonic() >= attempt_deadline:
                    raise TimeoutError("TDX minute request budget exhausted")
                adapter._save_server_state(
                    host=server[0], port=server[1], name=server[2],
                    backend=backend, persist=False,
                )
                return rows
            except Exception as exc:
                last_error = exc
                if quarantine and not isinstance(exc, ImportError):
                    adapter._mark_server_failure(server, persist=False)
                logger.debug("Isolated TDX minute attempt failed (%s)", type(exc).__name__)
            finally:
                adapter._close_api(api, backend)
        if time.monotonic() >= deadline:
            raise TimeoutError("TDX minute request budget exhausted") from last_error
        raise ConnectionError("TDX minute source unavailable") from last_error

    def _connect_pytdx(self) -> bool:
        return self._connect_candidates("pytdx")

    def _connect_manual(self) -> bool:
        return self._connect_candidates("mootdx")

    def _open_api(self, server: tuple[str, int, str], backend: str, deadline: float):
        """Build mootdx without BaseQuotes.config.setup()/implicit bestip scans."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("TDX validation budget exhausted")
        if backend == "mootdx":
            from mootdx.quotes import StdQuotes, TdxHq_API
            api = StdQuotes.__new__(StdQuotes)
            api.server = server[:2]
            api.timeout = self.timeout
            api.verbose = False
            api.client = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=True)
            client = api.client
        else:
            from pytdx.hq import TdxHq_API
            api = client = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=True)
        try:
            # Setup contains multiple socket reads; each must share the same deadline.
            client.need_setup = False
            if not client.connect(server[0], server[1], time_out=min(self.timeout, remaining)):
                raise ConnectionError("TDX connect failed")
            client.client = self._DeadlineSocket(client.client, self.timeout, deadline)
            client.need_setup = True
            client.setup()
            return api
        except Exception:
            self._close_api(api, backend)
            raise

    @staticmethod
    def _close_api(api, backend: str) -> None:
        if api is None:
            return
        client = getattr(api, "client", None) if backend == "mootdx" else api
        sock = getattr(client, "client", None)
        try:
            if callable(getattr(client, "disconnect", None)):
                client.disconnect()
            elif callable(getattr(api, "close", None)):
                api.close()
        except Exception as exc:
            logger.debug("TDX connection close failed: %s", exc)
        finally:
            # Both libraries may erase client.client after a failed shutdown.
            if callable(getattr(sock, "close", None)):
                try:
                    sock.close()
                except OSError as exc:
                    logger.debug("TDX socket close failed: %s", exc)

    @staticmethod
    def _validate_probe_bars(data: pd.DataFrame) -> None:
        required = {"open", "high", "low", "close"}
        date_column = next((c for c in ("datetime", "trade_date", "date") if c in data), None)
        if date_column is None or not required.issubset(data.columns):
            raise ConnectionError("TDX probe lacks daily dates/OHLC")
        dates = pd.to_datetime(data[date_column], errors="coerce")
        today = pd.Timestamp.now().normalize()
        values = data[list(required)].apply(pd.to_numeric, errors="coerce")
        if (dates.isna().any() or (dates > today + pd.Timedelta(days=1)).any()
                or dates.max().normalize() > today
                or dates.max() < today - pd.Timedelta(days=14)
                or not values.map(lambda v: math.isfinite(v) and v > 0).all().all()
                or (values["high"] < values[["open", "close", "low"]].max(axis=1)).any()
                or (values["low"] > values[["open", "close", "high"]].min(axis=1)).any()):
            raise ConnectionError("TDX probe daily date/OHLC validation failed")

    def _validate_node(self, api, backend: str, deadline: float) -> bool:
        fingerprints = set()
        for market, symbol in ((MARKET_SH, "600519"), (MARKET_SZ, "000001"), (MARKET_BJ, "920799")):
            if time.monotonic() >= deadline:
                raise TimeoutError("TDX validation budget exhausted")
            client = getattr(api, "client", None) if backend == "mootdx" else api
            if callable(getattr(client, "get_security_bars", None)):
                raw = client.get_security_bars(CATEGORY_MAP["daily"], market, symbol, 0, 2)
                data = pd.DataFrame(raw) if raw is not None else None
            else:
                data = api.bars(symbol=symbol, market=market, frequency=4, start=0, offset=2)
            if bool(getattr(client, "last_transaction_failed", False)):
                raise ConnectionError("TDX probe transaction failed")
            # An empty, successful response is unverified capability, not proof of a bad node.
            if data is None or data.empty:
                return False
            self._validate_probe_bars(data)
            fingerprint = tuple(data.iloc[-1][["open", "high", "low", "close"]])
            if fingerprint in fingerprints:
                raise ConnectionError("TDX probe returned cloned market prices")
            fingerprints.add(fingerprint)
        return True

    def _accept_connection(self, api, backend: str, server: tuple[str, int, str]) -> None:
        client = getattr(api, "client", None) if backend == "mootdx" else api
        sock = getattr(client, "client", None)
        if isinstance(sock, self._DeadlineSocket):
            sock.deadline = None
            sock.sock.settimeout(self.timeout)
        self.api = api
        self.connected = True
        self.backend = backend
        self._active_server = server
        self.current_server = f"{server[0]}:{server[1]}({server[2]})[{backend}]"

    def _connect_candidates(self, backend: str) -> bool:
        if not hasattr(self, "_round_deadline"):
            self._round_deadline = time.monotonic() + self.VALIDATION_BUDGET_SECONDS
            self._round_attempts = 0
        for server in self._ordered_servers():
            if self._round_attempts >= self.MAX_CANDIDATES or time.monotonic() >= self._round_deadline:
                break
            self._round_attempts += 1
            with self._health_lock:
                state = self._health_state()
                state["cursor"] = (self.SERVERS.index(server) + 1) % len(self.SERVERS)
            probe = business = None
            try:
                probe = self._open_api(server, backend, self._round_deadline)
                verified = self._validate_node(probe, backend, self._round_deadline)
                self._close_api(probe, backend)
                probe = None
                if not verified:
                    continue
                business = self._open_api(server, backend, self._round_deadline)
                if time.monotonic() >= self._round_deadline:
                    raise TimeoutError("TDX validation budget exhausted")
                self._accept_connection(business, backend, server)
                return True
            except ImportError as exc:
                logger.debug("TDX backend unavailable: %s", exc)
                break
            except Exception as exc:
                self._mark_server_failure(server)
                logger.debug("TDX node %s:%s failed: %s", server[0], server[1], exc)
            finally:
                self._close_api(probe, backend)
                if business is not getattr(self, "api", None):
                    self._close_api(business, backend)
        self.api = None
        self.connected = False
        self.backend = None
        return False

    def fork_connection(self, *, connection_deadline: float) -> "MootdxAdapter":
        """Open another clean stream to this round's validated node, without another scan."""
        adapter = copy(self)
        adapter.api = None
        adapter.connected = False
        adapter.success_count = adapter.fail_count = 0
        adapter._connection_deadline = None
        server = getattr(self, "_active_server", None)
        if server is None or not self.is_available():
            return adapter
        api = None
        try:
            api = adapter._open_api(server, self.backend, connection_deadline)
            if time.monotonic() >= connection_deadline:
                raise TimeoutError("TDX pool startup budget exhausted")
            adapter._accept_connection(api, self.backend, server)
        except Exception as exc:
            adapter._mark_server_failure(server)
            adapter._close_api(api, self.backend)
            logger.debug("TDX pool stream startup failed: %s", exc)
        return adapter

    def _request_with_failover(
        self, operation: Callable[[], Optional[pd.DataFrame]],
    ) -> Optional[pd.DataFrame]:
        """One extra attempt per logical request, including nested qfq/xdxr requests."""
        if getattr(self, "_business_active", False):
            return operation()
        self._business_active = True
        try:
            attempts = 1 if getattr(self, "_pool_managed", False) else 2
            for attempt in range(attempts):
                result = operation()
                server = getattr(self, "_active_server", None)
                if self.last_failure_kind != "connectivity":
                    if result is not None and not result.empty and server is not None:
                        self._save_server_state(
                            host=server[0], port=server[1], name=server[2], backend=self.backend,
                        )
                    return result
                if server is None:
                    return None  # A failed discovery round must not itself be retried.
                self._mark_server_failure(server)
                self._discard_connection()
                if attempt == attempts - 1:
                    return None
            return None
        finally:
            self._business_active = False

    def _ensure_connected(self) -> bool:
        """Ensure the current connection is usable without sending a probe request."""
        if not self.connected or not self.api:
            return False if getattr(self, "_pool_managed", False) else self._connect()

        server = getattr(self, "_active_server", None)
        if server is not None and self._server_is_quarantined(server):
            self._discard_connection()
            return False if getattr(self, "_pool_managed", False) else self._connect()

        if self._transaction_failed():
            logger.warning("TDX 上一事务失败，丢弃连接后重连")
            server = getattr(self, "_active_server", None)
            if server is not None:
                self._mark_server_failure(server)
            self._discard_connection()
            return False if getattr(self, "_pool_managed", False) else self._connect()

        return True

    def _transaction_failed(self, error: Exception | None = None) -> bool:
        """Check the pytdx transaction behind either adapter backend."""
        # tdxpy does not maintain last_transaction_failed. Its exception wrapper
        # instead exposes the parser/socket error as original_exception.
        if error is not None:
            original = getattr(error, "original_exception", None)
            if original is not None or isinstance(error, (OSError, struct.error, zlib.error)):
                return True
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
        self._close_api(stale_api, stale_backend)

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
                data = self.api.index_bars(
                    symbol=code,
                    market=market_code,
                    frequency=frequency,
                    start=start,
                    offset=request_size,
                )

            if self._transaction_failed():
                self._discard_connection()
                raise ConnectionError("TDX index pagination transaction failed")

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
        """Fetch corporate actions with at most one failover retry."""
        return self._request_with_failover(lambda: self._get_xdxr_info_once(symbol))

    def _get_xdxr_info_once(self, symbol: str) -> Optional[pd.DataFrame]:
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
                "connectivity" if isinstance(e, ConnectionError) or self._transaction_failed(e)
                else "request"
            )
            self._discard_connection()
            return None

    def get_recent_stock_bars(
        self, symbol: str, period: str = "5min", count: int = 3,
    ) -> Optional[pd.DataFrame]:
        """Fetch bounded raw intraday bars without daily truncation or qfq work."""
        if period not in {"1min", "5min", "15min", "30min", "60min"}:
            raise ValueError(f"unsupported intraday period: {period}")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 800:
            raise ValueError("count must be between 1 and 800")
        if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
            raise ValueError("symbol must contain six ASCII digits")

        def fetch() -> Optional[pd.DataFrame]:
            self.last_failure_kind = None
            if not self._ensure_connected():
                self.last_failure_kind = "connectivity"
                self.fail_count += 1
                return None
            try:
                frame = self._fetch_bars_data(
                    symbol=symbol, period=period, batch_size=count, max_bars=count,
                )
                if frame is None or frame.empty:
                    raise ConnectionError("empty intraday response")
                frame = _normalize_volume_aliases(frame)
                frame = frame.rename(columns={"datetime": "bar_time"})
                frame["bar_time"] = pd.to_datetime(frame["bar_time"], errors="coerce")
                frame = frame.dropna(subset=["bar_time"])
                if frame.empty:
                    self.last_failure_kind = "invalid_data"
                    self.fail_count += 1
                    return None
                frame["symbol"] = symbol
                self.success_count += 1
                return frame.sort_values("bar_time").drop_duplicates(
                    subset=["bar_time"], keep="last",
                ).reset_index(drop=True)
            except Exception as exc:
                self.fail_count += 1
                self.last_failure_kind = (
                    "connectivity" if isinstance(exc, ConnectionError)
                    or self._transaction_failed(exc) else "invalid_data"
                )
                logger.warning("mootdx %s %s failed: %s", symbol, period,
                               getattr(exc, "original_exception", None) or exc)
                return None

        return self._request_with_failover(fetch)

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily",
        adjust: str = "qfq",
        max_bars: int | None = None,
    ) -> Optional[pd.DataFrame]:
        """Fetch bars with one failover for the entire bars + qfq action request."""
        return self._request_with_failover(lambda: self._get_stock_kline_once(
            symbol, start_date, end_date, period, adjust, max_bars,
        ))

    def _get_stock_kline_once(
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
                if self.last_failure_kind in {"connectivity", "request"}:
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
                "connectivity" if isinstance(e, ConnectionError) or self._transaction_failed(e)
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
        """Restart index pagination from page zero on one clean replacement stream."""
        return self._request_with_failover(lambda: self._get_index_kline_once(
            symbol, start_date, end_date, period, market, max_bars,
        ))

    def _get_index_kline_once(
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
        self.last_failure_kind = None
        if not self._ensure_connected():
            self.last_failure_kind = "connectivity"
            return None

        try:
            code = str(symbol).zfill(6)
            try:
                df = self._fetch_index_bars_data(
                    code, period=period, market=market, max_bars=max_bars,
                )
            except Exception as exc:
                raise ConnectionError("TDX index pagination failed") from exc
            if df is None or df.empty:
                self.last_failure_kind = "no_data"
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
            self.last_failure_kind = (
                "connectivity" if isinstance(e, ConnectionError) or self._transaction_failed(e)
                else "request"
            )
            self._discard_connection()
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
        period: str = "daily",
    ) -> Optional[pd.DataFrame]:
        """Use the same single-retry transaction boundary for sector index bars."""
        return self._request_with_failover(lambda: self._get_sector_kline_once(
            sector_symbol, start_date, end_date, period,
        ))

    def _get_sector_kline_once(
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
        self.last_failure_kind = None
        if not self._ensure_connected():
            self.last_failure_kind = "connectivity"
            return None

        try:
            code = str(sector_symbol).zfill(6)
            try:
                df = self._fetch_index_bars_data(code, period=period)
            except Exception as exc:
                raise ConnectionError("TDX sector index pagination failed") from exc
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
            self.last_failure_kind = (
                "connectivity" if isinstance(e, ConnectionError) or self._transaction_failed(e)
                else "request"
            )
            self._discard_connection()
            return None

    def disconnect(self):
        """断开连接"""
        self._discard_connection()

    def __del__(self):
        """析构函数，确保断开连接"""
        self.disconnect()


def get_mootdx_adapter() -> Optional[MootdxAdapter]:
    """获取 mootdx 适配器单例"""
    with MootdxAdapter._instance_lock:
        if MootdxAdapter._instance is None:
            MootdxAdapter._instance = MootdxAdapter()
        elif not MootdxAdapter._instance.is_available():
            MootdxAdapter._instance._connect()
        adapter = MootdxAdapter._instance
        return adapter if adapter.is_available() else None


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
