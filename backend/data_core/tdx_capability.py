"""TDX source capability probe for Data Core."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from data_core.bse_codes import TDX_BSE_MARKET, TDX_STANDARD_BJ_SOURCE_NAME, to_bse_tdx_symbol
from data_core.schema import ensure_data_core_schema, log_data_source_run
from data_job.core.mootdx_adapter import MootdxAdapter


STANDARD_SAMPLE_SYMBOLS = {
    "SZ": ("000001",),
    "SH": ("600519",),
}
BSE_STANDARD_SAMPLE_SYMBOLS = ("920002", "920008", "920016", "430047", "830799", "871396")


def parse_max_servers(value: str | int | None) -> int | None:
    """Compatibility parser for the deprecated EXHQ argument."""
    if value is None:
        return 3
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text == "all":
        return None
    return int(text)


def probe_standard_tdx(
    *,
    samples: dict[str, tuple[str, ...]] = STANDARD_SAMPLE_SYMBOLS,
    timeout: float = 2.0,
    max_bars: int = 2,
) -> dict[str, object]:
    """Smoke test standard TDX K-line coverage for SH/SZ samples."""
    started = time.perf_counter()
    sample_results: dict[str, object] = {}
    success_count = 0
    fail_count = 0
    adapter = MootdxAdapter(timeout=max(1, int(timeout)))

    try:
        if not adapter.is_available():
            total = sum(len(items) for items in samples.values())
            return {
                "status": "FAIL",
                "success_count": 0,
                "fail_count": total,
                "success_rate": 0.0,
                "error_message": "standard TDX adapter unavailable",
                "samples": {},
                "duration": round(time.perf_counter() - started, 3),
            }

        for market, symbols in samples.items():
            for symbol in symbols:
                key = f"{market}:{symbol}"
                df = adapter.get_stock_kline(symbol, adjust="none", max_bars=max_bars)
                if df is not None and not df.empty:
                    success_count += 1
                    sample_results[key] = {
                        "ok": True,
                        "rows": int(len(df)),
                        "backend": adapter.backend,
                        "server": adapter.current_server,
                    }
                else:
                    fail_count += 1
                    sample_results[key] = {"ok": False, "reason": "no_bars"}
    finally:
        adapter.disconnect()

    total = success_count + fail_count
    success_rate = success_count / total if total else 0.0
    status = "PASS" if total > 0 and success_rate >= 0.95 else "FAIL"
    return {
        "status": status,
        "success_count": success_count,
        "fail_count": fail_count,
        "success_rate": success_rate,
        "error_message": None if status == "PASS" else "standard TDX samples not covered",
        "samples": sample_results,
        "duration": round(time.perf_counter() - started, 3),
    }


def probe_bse_standard_tdx(
    *,
    samples: tuple[str, ...] = BSE_STANDARD_SAMPLE_SYMBOLS,
    timeout: float = 2.0,
    max_bars: int = 2,
) -> dict[str, object]:
    """Smoke test BSE daily bars via standard TDX market=2."""
    started = time.perf_counter()
    sample_results: dict[str, object] = {}
    success_count = 0
    fail_count = 0
    adapter = MootdxAdapter(timeout=max(1, int(timeout)))

    try:
        if not adapter.is_available():
            return {
                "status": "FAIL",
                "success_count": 0,
                "fail_count": len(samples),
                "success_rate": 0.0,
                "error_message": "standard TDX adapter unavailable for BSE market=2",
                "tdx_market": TDX_BSE_MARKET,
                "samples": {},
                "duration": round(time.perf_counter() - started, 3),
            }

        for symbol in samples:
            request_symbol = to_bse_tdx_symbol(symbol)
            df = adapter.get_stock_kline(symbol, adjust="none", max_bars=max_bars)
            if df is not None and not df.empty:
                success_count += 1
                sample_results[symbol] = {
                    "ok": True,
                    "rows": int(len(df)),
                    "tdx_market": TDX_BSE_MARKET,
                    "request_symbol": request_symbol,
                    "backend": adapter.backend,
                    "server": adapter.current_server,
                    "latest_trade_date": str(df["trade_date"].max()) if "trade_date" in df.columns else None,
                }
            else:
                fail_count += 1
                sample_results[symbol] = {
                    "ok": False,
                    "reason": "no_bars",
                    "tdx_market": TDX_BSE_MARKET,
                    "request_symbol": request_symbol,
                }
    finally:
        adapter.disconnect()

    total = success_count + fail_count
    success_rate = success_count / total if total else 0.0
    status = "PASS" if total > 0 and success_rate >= 0.95 else "FAIL"
    return {
        "status": status,
        "success_count": success_count,
        "fail_count": fail_count,
        "success_rate": success_rate,
        "error_message": None if status == "PASS" else "BSE standard TDX samples not covered",
        "tdx_market": TDX_BSE_MARKET,
        "samples": sample_results,
        "duration": round(time.perf_counter() - started, 3),
    }


def persist_capability_result(
    db_path: str | Path,
    *,
    run_id: str,
    trade_date: str | None,
    result: dict[str, object],
) -> None:
    ensure_data_core_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        log_data_source_run(
            conn,
            run_id=run_id,
            trade_date=trade_date,
            source=TDX_STANDARD_BJ_SOURCE_NAME,
            api_name="bse_standard_capability_probe",
            market="BSE",
            status=str(result["status"]),
            success_count=int(result["success_count"]),
            fail_count=int(result["fail_count"]),
            error_message=result.get("error_message"),
            start_time=None,
            end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            duration=float(result.get("duration") or 0),
            detail_json=json.dumps(result, ensure_ascii=False),
        )
        conn.commit()


def persist_standard_capability_result(
    db_path: str | Path,
    *,
    run_id: str,
    trade_date: str | None,
    result: dict[str, object],
) -> None:
    ensure_data_core_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        log_data_source_run(
            conn,
            run_id=run_id,
            trade_date=trade_date,
            source="tdx_mootdx_qfq",
            api_name="standard_capability_probe",
            market="SH_SZ",
            status=str(result["status"]),
            success_count=int(result["success_count"]),
            fail_count=int(result["fail_count"]),
            error_message=result.get("error_message"),
            start_time=None,
            end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            duration=float(result.get("duration") or 0),
            detail_json=json.dumps(result, ensure_ascii=False),
        )
        conn.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe TDX source capability")
    parser.add_argument("--db", default=settings.LOCAL_DB_PATH, help="SQLite database path")
    parser.add_argument("--run-id", default=datetime.now().strftime("capability_%Y%m%d_%H%M%S"))
    parser.add_argument("--trade-date", help="optional trade date")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--max-servers", default="3", help="deprecated compatibility option; BSE uses standard TDX")
    parser.add_argument("--fail-on-fail", action="store_true")
    parser.add_argument(
        "--fail-on-all-fail",
        action="store_true",
        help="return non-zero only when both standard SH/SZ and BSE probes fail",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    standard_result = probe_standard_tdx(timeout=args.timeout)
    persist_standard_capability_result(
        args.db,
        run_id=args.run_id,
        trade_date=args.trade_date,
        result=standard_result,
    )

    parse_max_servers(args.max_servers)
    result = probe_bse_standard_tdx(timeout=args.timeout)
    persist_capability_result(
        args.db,
        run_id=args.run_id,
        trade_date=args.trade_date,
        result=result,
    )
    combined = {
        "standard": standard_result,
        "bse": result,
    }
    print(json.dumps(combined, ensure_ascii=False, indent=2))
    if (
        args.fail_on_all_fail
        and standard_result["status"] == "FAIL"
        and result["status"] == "FAIL"
    ):
        return 1
    if args.fail_on_fail and (standard_result["status"] == "FAIL" or result["status"] == "FAIL"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
