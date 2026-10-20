"""Bounded, read-only minute charts; never shares the monitor's TDX socket."""
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import logging
import math
import re
import threading
import time
from zoneinfo import ZoneInfo

import requests

from data_core.market_calendar import is_a_share_session

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_INPUT = 1040
MAX_CACHE_BYTES = 8 * 1024 * 1024


class MinuteChartUnavailable(RuntimeError):
    """Source failed, timed out, or did not cover the requested date."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MinuteChartUnavailable("分钟行情请求超时")
    return min(2.0, remaining)


def _normalize_bars(raw: list[dict], now: datetime, end_date: date | None,
                    base_minutes: int) -> list[dict]:
    """Reject invalid OHLC and keep only completed regular-session bars."""
    if len(raw) > MAX_INPUT:
        raise MinuteChartUnavailable("分钟行情源超过输入上限")
    accepted = {}
    allowed = {600, 630, 660, 690, 810, 840, 870, 900}
    if base_minutes == 60:
        allowed = {630, 690, 840, 900}
    for item in raw:
        try:
            stamp = datetime.fromisoformat(str(item.get("bar_time", "")))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=SHANGHAI)
            stamp = stamp.astimezone(SHANGHAI)
            minute = stamp.hour * 60 + stamp.minute
            if stamp.second or minute not in allowed or not is_a_share_session(stamp.date()):
                continue
            # Buffer publication at boundaries; a forming bar is never a full bar.
            if stamp + timedelta(seconds=30) > now:
                continue
            if end_date is not None and stamp.date() > end_date:
                continue
            numbers = {key: float(item[key]) for key in
                       ("open", "high", "low", "close", "volume", "amount")}
            if not all(math.isfinite(value) for value in numbers.values()):
                raise ValueError("non-finite OHLCV")
            if min(numbers[key] for key in ("open", "high", "low", "close")) <= 0:
                raise ValueError("non-positive price")
            if min(numbers["volume"], numbers["amount"]) < 0:
                raise ValueError("negative volume")
            if not (numbers["low"] <= min(numbers["open"], numbers["close"])
                    <= max(numbers["open"], numbers["close"]) <= numbers["high"]):
                raise ValueError("invalid OHLC envelope")
            key = stamp.strftime("%Y-%m-%d %H:%M")
            if key in accepted and accepted[key] != {"trade_date": key, **numbers}:
                raise ValueError("conflicting timestamp")
            accepted[key] = {"trade_date": key, **numbers}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MinuteChartUnavailable("分钟行情源包含无效时间或 OHLCV") from exc
    return [accepted[key] for key in sorted(accepted)]


def aggregate_120min(bars: list[dict]) -> list[dict]:
    """Aggregate exactly 10:30+11:30 or 14:00+15:00, without crossing lunch."""
    buckets = {}
    for item in bars:
        stamp = datetime.fromisoformat(item["trade_date"])
        minute = stamp.hour * 60 + stamp.minute
        if minute not in {630, 690, 840, 900}:
            continue
        key = (stamp.date(), "am" if minute <= 690 else "pm")
        buckets.setdefault(key, {})[minute] = item
    result = []
    for (_, session), items in sorted(buckets.items()):
        expected = [630, 690] if session == "am" else [840, 900]
        if sorted(items) != expected:
            continue
        first, last = (items[minute] for minute in expected)
        result.append({
            "trade_date": last["trade_date"], "open": first["open"],
            "high": max(row["high"] for row in items.values()),
            "low": min(row["low"] for row in items.values()), "close": last["close"],
            "volume": sum(row["volume"] for row in items.values()),
            "amount": sum(row["amount"] for row in items.values()),
        })
    return result


def _tdx_bars(kind: str, symbol: str, market: int, minutes: int,
              count: int, deadline: float) -> tuple[list[dict], str, str]:
    from data_job.core.tdx_adapter import TDX_SERVERS
    from pytdx.hq import TdxHq_API

    # A new connection for every attempt; no probe or pooled monitor stream.
    for host, port in TDX_SERVERS[:2]:
        api = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=True)
        try:
            if not api.connect(host, port, time_out=_remaining(deadline)):
                continue
            rows = []
            method = api.get_index_bars if kind == "index" else api.get_security_bars
            for offset in range(0, count, 800):
                timeout = _remaining(deadline)
                if getattr(api, "client", None) is not None:
                    api.client.settimeout(timeout)
                page = method(2 if minutes == 30 else 3, market, symbol,
                              offset, min(800, count - offset))
                if not page:
                    raise MinuteChartUnavailable("通达信分钟行情为空")
                rows.extend(page)
                if len(page) < min(800, count - offset):
                    break
            raw = []
            for row in rows:
                # TDX's binary bars carry no symbol; an isolated request stream is mandatory.
                if row.get("code") is not None and str(row["code"]) != symbol:
                    raise MinuteChartUnavailable("通达信返回证券代码不一致")
                raw.append({"bar_time": row.get("datetime"), "open": row.get("open"),
                            "high": row.get("high"), "low": row.get("low"),
                            "close": row.get("close"), "volume": row.get("vol"),
                            "amount": row.get("amount")})
            # Keep native TDX volume. Index and stock native units must not be guessed.
            return raw, "tdx_index" if kind == "index" else "tdx_stock", "tdx_native"
        except Exception as exc:
            logger.debug("Isolated minute TDX request failed (%s)", type(exc).__name__)
            _remaining(deadline)
        finally:
            try:
                api.disconnect()
            except Exception as exc:
                logger.debug("Minute TDX disconnect failed (%s)", type(exc).__name__)
    raise MinuteChartUnavailable("通达信分钟行情暂不可用")


def _eastmoney_bars(symbol: str, minutes: int, count: int,
                    deadline: float) -> tuple[list[dict], str, str]:
    params = {"secid": f"90.{symbol}", "fields1": "f1,f2,f3,f4,f5,f6",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
              "klt": str(minutes), "fqt": "0", "lmt": str(count),
              "beg": "0", "end": "20500101"}
    with requests.Session() as session:
        # Match the existing direct Eastmoney clients; do not inherit unrelated
        # shell/LaunchAgent proxies into this isolated market-data request.
        session.trust_env = False
        session.proxies.clear()
        with session.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                         params=params, timeout=_remaining(deadline), stream=True,
                         headers={
                             "User-Agent": (
                                 "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/124.0 Safari/537.36"
                             ),
                             "Referer": "https://quote.eastmoney.com/",
                         }) as response:
            response.raise_for_status()
            chunks = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                _remaining(deadline)
                chunks.extend(chunk)
                if len(chunks) > 1024 * 1024:
                    raise MinuteChartUnavailable("板块分钟响应超过大小上限")
    payload = json.loads(chunks)
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("code") != symbol:
        raise MinuteChartUnavailable("板块分钟行情为空或代码不一致")
    lines = data.get("klines") or []
    if not isinstance(lines, list) or len(lines) > MAX_INPUT:
        raise MinuteChartUnavailable("板块分钟行情超过输入上限")
    raw = []
    for line in lines:
        parts = str(line).split(",")
        if len(parts) < 7:
            raise MinuteChartUnavailable("板块分钟行情字段缺失")
        raw.append({"bar_time": parts[0], "open": parts[1], "close": parts[2],
                    "high": parts[3], "low": parts[4], "volume": parts[5],
                    "amount": parts[6]})
    # Same raw BK endpoint and volume convention as the existing sector collector.
    return raw, "eastmoney_bk", "lots"


class MinuteChartService:
    """At most two source requests and bounded successful-result LRU storage."""
    _shared_lock = threading.Lock()
    _shared_instance = None

    @classmethod
    def shared(cls):
        """Initialize exactly once, including concurrent cold-start requests."""
        with cls._shared_lock:
            if cls._shared_instance is None:
                cls._shared_instance = cls()
            return cls._shared_instance

    def __init__(self):
        self._slots = threading.BoundedSemaphore(2)
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self._cache_bytes = 0

    def load(self, kind: str, symbol: str, period: str, end_date: str | None = None,
             limit: int = 260, market: int | None = None,
             sector_type: str | None = None) -> dict:
        started = time.monotonic()
        now = datetime.now(SHANGHAI)
        if kind not in {"stock", "index", "sector"} or period not in {"30min", "120min"}:
            raise ValueError("unsupported minute chart kind or period")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 30 <= limit <= 520:
            raise ValueError("minute chart limit must be between 30 and 520")
        symbol = str(symbol).strip()
        pattern = r"BK\d{4}" if kind == "sector" else r"\d{6}"
        if not re.fullmatch(pattern, symbol):
            raise ValueError("invalid minute chart symbol")
        if kind == "index" and market not in {0, 1}:
            raise ValueError("index market must be explicitly 0 (SZ) or 1 (SH)")
        if kind == "sector" and str(sector_type or "industry").lower() not in {
                "industry", "concept"}:
            raise ValueError("unsupported sector type")
        cutoff = None if end_date in {None, "latest"} else date.fromisoformat(end_date)
        if cutoff is not None and cutoff > now.date():
            raise ValueError("minute chart end_date cannot be in the future")
        source = "eastmoney_bk" if kind == "sector" else f"tdx_{kind}"
        result = {"status": "unavailable", "ready": False, "message": "",
                  "period": period, "source": source, "adjustment": "none",
                  "volume_unit": "lots" if kind == "sector" else "tdx_native",
                  "bars": [], "source_count": 0, "cache_hit": False}
        key = (kind, symbol, period, cutoff, limit, market, sector_type)
        with self._lock:
            for stale in [key for key, value in self._cache.items() if value[0] <= started]:
                self._cache_bytes -= self._cache.pop(stale)[2]
            if key in self._cache:
                self._cache.move_to_end(key)
                cached = deepcopy(self._cache[key][1])
                cached.update(cache_hit=True, elapsed_ms=round((time.monotonic()-started)*1000, 2))
                return cached
        if (kind == "stock" and symbol.startswith(("4", "8", "92"))) or (
                kind == "index" and symbol == "899050"):
            result["message"] = "北交所分钟行情源尚未验证，暂不可用"
        elif not self._slots.acquire(blocking=False):
            result["message"] = "分钟行情请求繁忙，请稍后重试"
        else:
            try:
                # A 10-second source budget plus at most a 2-second active read
                # keeps the overall request within its 12-second budget.
                minutes = 30 if period == "30min" else 60
                count = min(MAX_INPUT, limit + 1 if minutes == 30 else limit * 2 + 2)
                if kind == "sector":
                    raw, source, unit = _eastmoney_bars(symbol, minutes, count, started + 10)
                else:
                    resolved_market = market if kind == "index" else (
                        1 if symbol.startswith(("5", "6", "9")) else 0
                    )
                    raw, source, unit = _tdx_bars(kind, symbol, resolved_market, minutes,
                                                count, started + 10)
                bars = _normalize_bars(raw, now, cutoff, minutes)
                if period == "120min":
                    bars = aggregate_120min(bars)
                if not bars or (cutoff and bars[-1]["trade_date"][:10] != cutoff.isoformat()):
                    raise MinuteChartUnavailable("指定日期无完整分钟K线或超出行情源历史窗口")
                for index, row in enumerate(bars):
                    previous = bars[index-1]["close"] if index else None
                    row["pct_chg"] = (row["close"] / previous - 1) * 100 if previous else None
                result.update(status="ready", ready=True, source=source, volume_unit=unit,
                              bars=bars[-limit:], source_count=len(raw), message="仅显示已完成柱；未复权")
                if unit == "lots":
                    for row in result["bars"]:
                        row["volume"] *= 100
                    result["volume_unit"] = "shares"
            except Exception as exc:
                if isinstance(exc, MinuteChartUnavailable):
                    result["message"] = str(exc)
                elif isinstance(exc, requests.Timeout):
                    result["message"] = "分钟行情源连接或读取超时，请稍后重试"
                elif isinstance(exc, requests.ConnectionError):
                    result["message"] = "分钟行情源连接中断，当前不可用"
                else:
                    result["message"] = "分钟行情源请求失败，请稍后重试"
                logger.debug("Minute chart request failed (%s)", type(exc).__name__)
            finally:
                self._slots.release()
        result["elapsed_ms"] = round((time.monotonic()-started)*1000, 2)
        if result["ready"]:
            size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            with self._lock:
                while self._cache and (len(self._cache) >= 32
                                       or self._cache_bytes + size > MAX_CACHE_BYTES):
                    _, (_, _, old_size) = self._cache.popitem(last=False)
                    self._cache_bytes -= old_size
                if size <= MAX_CACHE_BYTES:
                    if key in self._cache:
                        self._cache_bytes -= self._cache.pop(key)[2]
                    self._cache[key] = (time.monotonic()+120, deepcopy(result), size)
                    self._cache_bytes += size
        return result

    def resource_stats(self) -> dict:
        """Return cache accounting without creating threads, timers, or connections."""
        with self._lock:
            return {"cache_entries": len(self._cache), "cache_bytes": self._cache_bytes,
                    "max_concurrent_requests": 2, "max_input_bars": MAX_INPUT}


def _service() -> MinuteChartService:
    return MinuteChartService.shared()


def get_minute_chart(kind: str, symbol: str, period: str, end_date: str | None = None,
                     limit: int = 260, market: int | None = None,
                     sector_type: str | None = None) -> dict:
    """Fetch real completed minute bars without disk writes or background work."""
    return _service().load(kind, symbol, period, end_date, limit, market, sector_type)
