"""Direct public-data clients used by enrichment collectors.

These helpers intentionally stay small and dependency-light.  They provide
stable wrappers around the public endpoints referenced by a-stock-data while
keeping AkShare as a fallback at the collector layer.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

EASTMONEY_UT = "7eea3edcaed734bea9cbfc24409ed989"


def safe_float(value: Any) -> float | None:
    """Parse a numeric value and return None for blanks, dashes and NaN."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if value in {"", "-", "--", "None", "nan", "NaN"}:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def normalize_symbol(symbol: str) -> str:
    return re.sub(r"\D", "", str(symbol or ""))[-6:].zfill(6)


def tencent_market_prefix(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def eastmoney_secid(symbol: str) -> str:
    code = normalize_symbol(symbol)
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    return f"{market}.{code}"


def _json_from_jsonp(text: str) -> Any:
    text = text.strip()
    match = re.search(r"\((.*)\)\s*;?$", text, flags=re.S)
    if match:
        text = match.group(1)
    return json.loads(text)


def parse_tencent_quote_payload(payload: str) -> list[dict[str, Any]]:
    """Parse qt.gtimg.cn quote payload.

    Tencent quote fields are positional and occasionally change.  We keep a
    conservative mapping and leave unsupported values as None instead of
    manufacturing numbers.
    """
    rows: list[dict[str, Any]] = []
    for match in re.finditer(r'v_[a-z]{2}\d{6}="([^"]*)"', payload):
        parts = match.group(1).split("~")
        if len(parts) < 4:
            continue
        symbol = normalize_symbol(parts[2])
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        trade_time = parts[30] if len(parts) > 30 else ""
        trade_date = None
        if len(trade_time) >= 8 and trade_time[:8].isdigit():
            trade_date = f"{trade_time[:4]}-{trade_time[4:6]}-{trade_time[6:8]}"

        total_mv = safe_float(parts[45]) if len(parts) > 45 else None
        circ_mv = safe_float(parts[44]) if len(parts) > 44 else None
        rows.append(
            {
                "code": symbol,
                "name": parts[1] if len(parts) > 1 else symbol,
                "trade_date": trade_date or datetime.now().strftime("%Y-%m-%d"),
                "price": safe_float(parts[3]),
                "pct_chg": safe_float(parts[32] if len(parts) > 32 else None),
                "turnover": safe_float(parts[38] if len(parts) > 38 else None),
                "volume_ratio": safe_float(parts[49] if len(parts) > 49 else None),
                "pe_ttm": safe_float(parts[39] if len(parts) > 39 else None),
                "pb": safe_float(parts[46] if len(parts) > 46 else None),
                # Tencent market values are commonly returned in 100M CNY.
                "total_mv": total_mv * 100000000 if total_mv is not None else None,
                "circ_mv": circ_mv * 100000000 if circ_mv is not None else None,
                "limit_up_price": safe_float(parts[47] if len(parts) > 47 else None),
                "limit_down_price": safe_float(parts[48] if len(parts) > 48 else None),
                "_source": "tencent_direct",
            }
        )
    return rows


def fetch_tencent_quotes(
    symbols: Iterable[str],
    *,
    chunk_size: int = 300,
    timeout: int = 10,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch Tencent direct quote snapshots for many A-share symbols."""
    codes = [tencent_market_prefix(symbol) for symbol in symbols if normalize_symbol(symbol)]
    if not codes:
        return pd.DataFrame()

    http = session or requests.Session()
    http.trust_env = False
    http.proxies.clear()
    http.headers.update(DEFAULT_HEADERS)
    rows: list[dict[str, Any]] = []
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        response = http.get(
            "https://qt.gtimg.cn/q=" + ",".join(chunk),
            timeout=timeout,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "gbk"
        rows.extend(parse_tencent_quote_payload(response.text))
        time.sleep(0.05 + random.random() * 0.08)
    return pd.DataFrame(rows)


@dataclass
class EastmoneyDirectClient:
    """Serial Eastmoney client with session reuse and jittered throttling."""

    min_interval: float = 1.0
    timeout: int = 12
    retries: int = 1

    def __post_init__(self) -> None:
        self.timeout = max(1, int(self.timeout))
        # Direct public endpoints should fail fast.  One retry is enough to
        # absorb a transient timeout without multiplying foreground latency.
        self.retries = min(1, max(0, int(self.retries)))
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(DEFAULT_HEADERS)
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def _wait_turn(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_interval - elapsed
            if wait > 0:
                time.sleep(wait + random.random() * 0.25)
            self._last_request_at = time.monotonic()

    def close(self) -> None:
        """Release pooled HTTP connections held by the reusable session."""
        self.session.close()

    def em_get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        attempts = self.retries + 1
        for attempt in range(attempts):
            self._wait_turn()
            try:
                response = self.session.get(url, params=params or {}, timeout=self.timeout)
                response.raise_for_status()
                text = response.text.strip()
                if not text:
                    return {}
                if text.startswith(("{", "[")):
                    return response.json()
                return _json_from_jsonp(text)
            except Exception as exc:  # noqa: BLE001 - preserve source failure as status.
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.6 + random.random() * 0.4)
        raise RuntimeError(f"Eastmoney request failed: {last_error}")

    @staticmethod
    def _f10_security_code(symbol: str) -> str:
        """Return Eastmoney's ``000001.SZ`` style security code."""
        raw = str(symbol or "").strip().upper()
        matched = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", raw)
        if matched:
            return f"{matched.group(1)}.{matched.group(2)}"

        digits = re.sub(r"\D", "", raw)
        if len(digits) != 6:
            raise ValueError(f"Invalid A-share symbol: {symbol!r}")
        if digits.startswith(("43", "83", "87", "92")):
            market = "BJ"
        elif digits.startswith(("5", "6", "9")):
            market = "SH"
        else:
            market = "SZ"
        return f"{digits}.{market}"

    def stock_f10_main_finance(self, symbol: str) -> dict[str, list[dict[str, Any]]]:
        """Fetch cumulative and real single-quarter F10 indicators.

        This deliberately issues exactly two bounded requests.  Both reports
        fit inside one 200-row page for the supported history horizon, so no
        pagination loop or implicit full-market fallback is needed.
        """
        security_code = self._f10_security_code(symbol)
        url = "https://datacenter.eastmoney.com/securities/api/data/get"
        common_params: dict[str, Any] = {
            "filter": f'(SECUCODE="{security_code}")',
            "p": 1,
            "ps": 200,
            "sr": -1,
            "st": "REPORT_DATE",
            "source": "HSF10",
            "client": "PC",
        }
        report_specs = (
            ("cumulative", "RPT_F10_FINANCE_MAINFINADATA", "APP_F10_MAINFINADATA"),
            ("quarterly", "RPT_F10_QTR_MAINFINADATA", "ALL"),
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for key, report_type, style in report_specs:
            payload = self.em_get(
                url,
                {
                    **common_params,
                    "type": report_type,
                    "sty": style,
                    "quoteColumns": "",
                },
            )
            data = (payload.get("result") or {}).get("data") or []
            result[key] = [row for row in data if isinstance(row, dict)]
        return result

    def datacenter(
        self,
        *,
        report_name: str,
        columns: str = "ALL",
        filter_value: str = "",
        sort_columns: str = "",
        sort_types: str = "-1",
        page_size: int = 50,
        page_number: int = 1,
    ) -> list[dict[str, Any]]:
        payload = self.em_get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "sortColumns": sort_columns,
                "sortTypes": sort_types,
                "pageSize": page_size,
                "pageNumber": page_number,
                "reportName": report_name,
                "columns": columns,
                "filter": filter_value,
                "source": "WEB",
                "client": "WEB",
            },
        )
        result = payload.get("result") or {}
        return result.get("data") or []

    def datacenter_pages(
        self,
        *,
        report_name: str,
        columns: str = "ALL",
        filter_value: str = "",
        sort_columns: str = "",
        sort_types: str = "-1",
        page_size: int = 50,
        max_pages: int = 3,
    ) -> list[dict[str, Any]]:
        """Fetch a small bounded set of Eastmoney datacenter pages."""
        rows: list[dict[str, Any]] = []
        for page_number in range(1, max(1, max_pages) + 1):
            page_rows = self.datacenter(
                report_name=report_name,
                columns=columns,
                filter_value=filter_value,
                sort_columns=sort_columns,
                sort_types=sort_types,
                page_size=page_size,
                page_number=page_number,
            )
            if not page_rows:
                break
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break
        return rows

    def push2(self, *, secid: str, fields: str = "f57,f58,f43,f169,f170") -> dict[str, Any] | None:
        payload = self.em_get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            {
                "ut": EASTMONEY_UT,
                "fltt": "2",
                "invt": "2",
                "secid": secid,
                "fields": fields,
            },
        )
        return payload.get("data") or None

    def limit_pool(self, pool: str, trade_date: str, page_size: int = 5000) -> list[dict[str, Any]]:
        endpoint_map = {
            "zt": "getTopicZTPool",
            "dt": "getTopicDTPool",
            "zb": "getTopicZBPool",
            "yzt": "getTopicYzTPool",
        }
        endpoint = endpoint_map[pool]
        payload = self.em_get(
            f"https://push2ex.eastmoney.com/{endpoint}",
            {
                "ut": EASTMONEY_UT,
                "dpt": "wz.ztzt",
                "Pageindex": 1,
                "pagesize": page_size,
                "sort": "fbt:asc",
                "date": trade_date,
            },
        )
        data = payload.get("data") or {}
        return data.get("pool") or []

    def stock_fund_flow_120d(self, symbol: str) -> list[dict[str, Any]]:
        code = normalize_symbol(symbol)
        market = "1" if code.startswith(("5", "6", "9")) else "0"
        payload = self.em_get(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            {
                "lmt": 120,
                "klt": 101,
                "secid": f"{market}.{code}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            },
        )
        klines = ((payload.get("data") or {}).get("klines") or [])
        rows: list[dict[str, Any]] = []
        for item in klines:
            parts = str(item).split(",")
            if len(parts) < 7:
                continue
            rows.append(
                {
                    "symbol": code,
                    "trade_date": parts[0],
                    "main_net": safe_float(parts[1]),
                    "small_net": safe_float(parts[2]),
                    "mid_net": safe_float(parts[3]),
                    "large_net": safe_float(parts[4]),
                    "super_net": safe_float(parts[5]),
                    "raw": item,
                }
            )
        return rows


def sina_financial_report(
    symbol: str,
    report_type: str,
    *,
    num: int = 8,
    session: requests.Session | None = None,
    timeout: int = 12,
) -> list[dict[str, Any]]:
    """Fetch Sina financial report rows.

    report_type uses Sina's short names: lrb, fzb, llb.
    """
    code = normalize_symbol(symbol)
    http = session or requests.Session()
    http.trust_env = False
    http.proxies.clear()
    http.headers.update(DEFAULT_HEADERS)
    response = http.get(
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getFinanceDataForAPP",
        params={"symbol": code, "type": report_type, "num": num},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = (
        _json_from_jsonp(response.text)
        if not response.text.strip().startswith("[")
        else response.json()
    )
    return payload if isinstance(payload, list) else []


class CNInfoDirectClient:
    """CNInfo announcement client with dynamic stock org-id mapping."""

    def __init__(self, timeout: int = 12) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                **DEFAULT_HEADERS,
                "Referer": "https://www.cninfo.com.cn/new/index",
                "Origin": "https://www.cninfo.com.cn",
            }
        )
        self._org_map: dict[str, dict[str, Any]] | None = None

    def org_map(self) -> dict[str, dict[str, Any]]:
        if self._org_map is not None:
            return self._org_map
        response = self.session.get(
            "https://www.cninfo.com.cn/new/data/szse_stock.json",
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json().get("stockList") or []
        self._org_map = {
            normalize_symbol(item.get("code")): item
            for item in data
            if normalize_symbol(item.get("code"))
        }
        return self._org_map

    def announcements(
        self,
        symbol: str,
        *,
        page_num: int = 1,
        page_size: int = 30,
    ) -> list[dict[str, Any]]:
        code = normalize_symbol(symbol)
        org_info = self.org_map().get(code)
        if not org_info:
            return []
        org_id = org_info.get("orgId") or org_info.get("orgid")
        response = self.session.post(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data={
                "pageNum": page_num,
                "pageSize": page_size,
                "column": "sse" if code.startswith(("6", "9")) else "szse",
                "tabName": "fulltext",
                "stock": f"{code},{org_id}",
                "seDate": "",
                "plate": "",
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("announcements") or []
