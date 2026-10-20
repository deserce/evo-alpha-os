"""TDX extended market helpers for Beijing Stock Exchange daily bars."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

import pandas as pd


BSE_SAMPLE_SYMBOLS = ("920002", "920008", "920016", "430047", "830799", "871396")
DEFAULT_EXHQ_SERVERS = (
    ("47.92.127.181", 7727, "exhq_beijing"),
    ("119.147.86.171", 7727, "exhq_shenzhen"),
    ("61.152.107.141", 7727, "exhq_shanghai_1"),
    ("61.152.107.171", 7727, "exhq_shanghai_2"),
)
FALLBACK_BSE_MARKETS = (27, 33, 34, 35, 36, 64)
TDX_EXHQ_SOURCE_NAME = "tdx_exhq"


@dataclass(frozen=True)
class ExhqServer:
    host: str
    port: int
    name: str


def _parse_env_servers(value: str | None = None) -> list[ExhqServer]:
    raw_value = os.getenv("TDX_EXHQ_SERVERS", "") if value is None else value
    servers: list[ExhqServer] = []
    for item in raw_value.split(","):
        text = item.strip()
        if not text:
            continue
        parts = [part.strip() for part in text.split(":")]
        if len(parts) < 2:
            continue
        try:
            port = int(parts[1])
        except ValueError:
            continue
        name = parts[2] if len(parts) >= 3 and parts[2] else f"env_exhq_{parts[0]}_{port}"
        servers.append(ExhqServer(parts[0], port, name))
    return servers


def _pytdx_future_servers() -> list[ExhqServer]:
    try:
        from pytdx.util.best_ip import future_ip
    except Exception:
        return []

    servers: list[ExhqServer] = []
    for item in future_ip:
        try:
            servers.append(
                ExhqServer(
                    host=str(item["ip"]),
                    port=int(item["port"]),
                    name=str(item.get("name") or f"future_{item['ip']}_{item['port']}"),
                )
            )
        except Exception:
            continue
    return servers


def discover_exhq_servers(include_future_ip: bool = True) -> tuple[ExhqServer, ...]:
    """Return configured EXHQ servers with env overrides first and duplicates removed."""
    candidates: list[ExhqServer] = []
    candidates.extend(_parse_env_servers())
    candidates.extend(ExhqServer(host, port, name) for host, port, name in DEFAULT_EXHQ_SERVERS)
    if include_future_ip:
        candidates.extend(_pytdx_future_servers())

    seen: set[tuple[str, int]] = set()
    deduped: list[ExhqServer] = []
    for server in candidates:
        key = (server.host, server.port)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(server)
    return tuple(deduped)


def parse_max_servers(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() == "all":
        return None
    parsed = int(value)
    return max(1, parsed)


def limit_servers(servers: Iterable[ExhqServer], max_servers: int | str | None) -> tuple[ExhqServer, ...]:
    items = tuple(servers)
    limit = parse_max_servers(max_servers)
    if limit is None:
        return items
    return items[:limit]


def candidate_markets(markets_raw) -> list[int]:
    candidates: list[int] = []
    for item in markets_raw or []:
        try:
            market = int(item.get("market"))
        except Exception:
            continue
        text = f"{item.get('name', '')} {item.get('short_name', '')}".upper()
        if "BSE" in text or "北" in text or "BJ" in text:
            candidates.append(market)

    for market in FALLBACK_BSE_MARKETS:
        if market not in candidates:
            candidates.append(market)
    return candidates


def _to_df(api, raw):
    if not raw:
        return None
    try:
        df = api.to_df(raw)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return df


def _normalize_exhq_bars(
    df: pd.DataFrame,
    *,
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    data = df.copy()
    if "datetime" in data.columns:
        data["trade_date"] = pd.to_datetime(data["datetime"].astype(str).str[:10], errors="coerce").dt.date
    elif {"year", "month", "day"}.issubset(data.columns):
        data["trade_date"] = pd.to_datetime(
            dict(year=data["year"], month=data["month"], day=data["day"]),
            errors="coerce",
        ).dt.date
    else:
        return pd.DataFrame()

    data = data.dropna(subset=["trade_date"])
    if start_date is not None:
        data = data[data["trade_date"] >= start_date]
    if end_date is not None:
        data = data[data["trade_date"] <= end_date]
    if data.empty:
        return pd.DataFrame()

    data = data.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
    output = pd.DataFrame()
    output["trade_date"] = data["trade_date"]
    for column in ("open", "high", "low", "close"):
        output[column] = pd.to_numeric(data.get(column), errors="coerce")

    volume_source = None
    for candidate in ("volume", "trade", "zongliang", "position"):
        if candidate in data.columns:
            volume_source = candidate
            break
    output["volume"] = pd.to_numeric(data.get(volume_source), errors="coerce") if volume_source else None
    output["amount"] = pd.to_numeric(data.get("amount"), errors="coerce") if "amount" in data.columns else None
    output["pct_chg"] = output["close"].pct_change() * 100.0
    output["pct_chg"] = output["pct_chg"].fillna(0.0)
    output["turnover_rate"] = None
    output["symbol"] = symbol
    return output


class TdxExHqAdapter:
    """Small pytdx EXHQ adapter for BSE daily bars.

    EXHQ does not expose A-share adjustment or turnover metadata through the
    same interface, so rows are written as unadjusted daily bars.
    """

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        servers: Iterable[ExhqServer] | None = None,
        max_servers: int | str | None = None,
        api_factory: Callable[[], object] | None = None,
    ):
        self.timeout = timeout
        self.servers = limit_servers(servers or discover_exhq_servers(), max_servers)
        self.api_factory = api_factory
        self.api = None
        self.current_server: ExhqServer | None = None
        self.markets: list[int] = []
        self.last_error = ""

    def _new_api(self):
        if self.api_factory is not None:
            return self.api_factory()
        from pytdx.exhq import TdxExHq_API

        return TdxExHq_API(heartbeat=False, auto_retry=False, raise_exception=False)

    def connect(self) -> bool:
        if self.api is not None:
            return True

        errors: list[str] = []
        for server in self.servers:
            api = self._new_api()
            try:
                if not api.connect(server.host, server.port, time_out=self.timeout):
                    errors.append(f"{server.name}: connect_failed")
                    continue
                markets_raw = api.get_markets() or []
                self.markets = candidate_markets(markets_raw)
                self.api = api
                self.current_server = server
                return True
            except Exception as exc:
                errors.append(f"{server.name}: {type(exc).__name__}: {str(exc)[:120]}")
                try:
                    api.disconnect()
                except Exception:
                    pass

        self.last_error = "; ".join(errors[-5:]) or "no EXHQ server available"
        return False

    def is_available(self) -> bool:
        return self.connect()

    def disconnect(self) -> None:
        if self.api is not None:
            try:
                self.api.disconnect()
            except Exception:
                pass
        self.api = None
        self.current_server = None

    def get_stock_kline(
        self,
        symbol: str,
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        max_bars: int | None = None,
    ) -> pd.DataFrame | None:
        if not self.connect() or self.api is None:
            return None

        try:
            from pytdx.params import TDXParams
        except Exception as exc:
            self.last_error = f"pytdx params unavailable: {exc}"
            return None

        start_dt = pd.to_datetime(start_date).date() if start_date else None
        end_dt = pd.to_datetime(end_date).date() if end_date else None
        count = min(max(2, int(max_bars or 800)), 800)
        errors: list[str] = []

        for market in self.markets:
            try:
                raw = self.api.get_instrument_bars(
                    TDXParams.KLINE_TYPE_DAILY,
                    market,
                    symbol,
                    start=0,
                    count=count,
                )
                df = _to_df(self.api, raw)
                if df is None:
                    continue
                normalized = _normalize_exhq_bars(
                    df,
                    symbol=symbol,
                    start_date=start_dt,
                    end_date=end_dt,
                )
                if not normalized.empty:
                    return normalized
            except Exception as exc:
                errors.append(f"market={market}: {type(exc).__name__}: {str(exc)[:80]}")

        self.last_error = "; ".join(errors[-5:]) or "no BSE bars"
        return None


def probe_bse_tdx(
    *,
    samples: tuple[str, ...] = BSE_SAMPLE_SYMBOLS,
    timeout: float = 2.0,
    max_servers: int | str | None = 3,
    servers: Iterable[ExhqServer] | None = None,
    api_factory: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Try pytdx extended market APIs for BSE samples."""
    try:
        from pytdx.params import TDXParams
    except Exception as exc:
        return {
            "status": "FAIL",
            "success_count": 0,
            "fail_count": len(samples),
            "success_rate": 0.0,
            "error_message": f"pytdx exhq unavailable: {exc}",
            "samples": {},
        }

    started = time.perf_counter()
    candidates = limit_servers(servers or discover_exhq_servers(), max_servers)
    sample_results: dict[str, object] = {}
    server_errors: list[str] = []
    server_results: list[dict[str, object]] = []
    markets_seen: list[int] = []
    best_server: dict[str, object] | None = None

    def new_api():
        if api_factory is not None:
            return api_factory()
        from pytdx.exhq import TdxExHq_API

        return TdxExHq_API(heartbeat=False, auto_retry=False, raise_exception=False)

    for server in candidates:
        api = new_api()
        server_started = time.perf_counter()
        server_detail: dict[str, object] = {
            "name": server.name,
            "host": server.host,
            "port": server.port,
            "status": "FAIL",
            "success_count": 0,
            "markets": [],
            "error": None,
        }
        try:
            if not api.connect(server.host, server.port, time_out=timeout):
                server_errors.append(f"{server.name}: connect_failed")
                server_detail["error"] = "connect_failed"
                continue

            markets_raw = api.get_markets() or []
            markets = candidate_markets(markets_raw)
            markets_seen.extend([market for market in markets if market not in markets_seen])
            server_detail["markets"] = markets

            server_success = 0
            for symbol in samples:
                if symbol in sample_results and sample_results[symbol].get("ok"):
                    continue

                found = False
                for market in markets:
                    raw = api.get_instrument_bars(
                        TDXParams.KLINE_TYPE_DAILY,
                        market,
                        symbol,
                        start=0,
                        count=2,
                    )
                    df = _to_df(api, raw)
                    if df is not None and not df.empty:
                        sample_results[symbol] = {
                            "ok": True,
                            "market": market,
                            "server": server.name,
                            "rows": int(len(df)),
                        }
                        server_success += 1
                        found = True
                        break
                if not found:
                    sample_results.setdefault(symbol, {"ok": False, "reason": "no_bars"})

            server_detail["success_count"] = server_success
            server_detail["status"] = "PASS" if server_success > 0 else "NO_BARS"
            if best_server is None or server_success > int(best_server.get("success_count") or 0):
                best_server = dict(server_detail)

        except Exception as exc:
            error = f"{server.name}: {type(exc).__name__}: {str(exc)[:120]}"
            server_errors.append(error)
            server_detail["error"] = error
        finally:
            server_detail["duration"] = round(time.perf_counter() - server_started, 3)
            server_results.append(server_detail)
            try:
                api.disconnect()
            except Exception:
                pass

    success_count = sum(1 for item in sample_results.values() if item.get("ok"))
    fail_count = len(samples) - success_count
    success_rate = success_count / len(samples) if samples else 0.0
    status = "PASS" if success_rate >= 0.95 else "FAIL"
    error_message = None
    if status == "FAIL":
        error_message = "; ".join(server_errors[-5:]) or "BSE TDX samples not covered"

    return {
        "status": status,
        "success_count": success_count,
        "fail_count": fail_count,
        "success_rate": success_rate,
        "error_message": error_message,
        "markets_seen": sorted(set(markets_seen)),
        "best_server": best_server,
        "servers": server_results,
        "server_count": len(candidates),
        "samples": sample_results,
        "duration": round(time.perf_counter() - started, 3),
    }
