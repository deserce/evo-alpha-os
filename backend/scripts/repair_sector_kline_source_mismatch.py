"""Repair sector_daily_prices rows that mixed synthetic and Eastmoney index sources.

The script rebuilds the current Industry universe from Eastmoney BK plate K-lines.
It is dry-run by default; use --apply only after the quality report is clean.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings  # noqa: E402
from data_job.core.eastmoney_request_gate import (  # noqa: E402
    EastmoneyBudgetExceeded,
    EastmoneyCircuitOpen,
    EastmoneyRequestError,
    EastmoneyRequestGate,
)


DB_PATH = Path(settings.LOCAL_DB_PATH)
if not DB_PATH.is_absolute():
    DB_PATH = backend_dir / DB_PATH

REPORT_DIR = backend_dir / "logs" / "sector_kline_repair"
BACKUP_DIR = backend_dir / "data" / "backups"

EASTMONEY_URLS = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}
EASTMONEY_VOLUME_MULTIPLIER = 100.0
TARGET_TABLE = "sector_vendor_daily"
_EASTMONEY_GATE: EastmoneyRequestGate | None = None


@dataclass(frozen=True)
class Sector:
    name: str
    code: str
    source: str


@dataclass
class FetchResult:
    sector_name: str
    sector_code: str
    ok: bool
    rows: list[dict[str, Any]]
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild current Industry sector K-lines from Eastmoney BK plates."
    )
    parser.add_argument("--start-date", default="2023-01-01", help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD or YYYYMMDD; default=max DB date")
    parser.add_argument("--apply", action="store_true", help="write verified Eastmoney rows to sector_vendor_daily")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="deprecated; requests are always serialized through the global gate",
    )
    parser.add_argument("--retries", type=int, default=1, help="deprecated; gate performs one reconnect retry")
    parser.add_argument(
        "--retry-failed-rounds",
        type=int,
        default=0,
        help="deprecated; additional retry rounds are disabled",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.5,
        help="minimum gate interval; values below 1.5 seconds are raised to 1.5",
    )
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout seconds")
    parser.add_argument(
        "--disable-rescaled-fallback",
        action="store_true",
        help="compatibility flag; legacy rescaling is always disabled",
    )
    parser.add_argument(
        "--legacy-only",
        action="store_true",
        help="deprecated; legacy rows are never written to the vendor table",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="scan existing sector_daily_prices quality and exit non-zero on hard issues",
    )
    parser.add_argument(
        "--repair-if-needed",
        action="store_true",
        help="scan first; report hard issues without applying legacy rescale repair",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=0,
        help="abort apply when failed sectors exceed this count",
    )
    parser.add_argument(
        "--sector",
        action="append",
        default=[],
        help="optional sector_name filter; can be repeated",
    )
    return parser.parse_args()


def date_compact(value: str) -> str:
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").strftime("%Y%m%d")


def date_text(value: str) -> str:
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").strftime("%Y-%m-%d")


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"database not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def eastmoney_gate(request_delay: float = 1.5, timeout: int = 15) -> EastmoneyRequestGate:
    """Return the process-wide gate used by every repair request."""

    global _EASTMONEY_GATE
    if _EASTMONEY_GATE is None:
        _EASTMONEY_GATE = EastmoneyRequestGate(
            min_interval=max(1.5, float(request_delay)),
            timeout=timeout,
        )
    return _EASTMONEY_GATE


def load_sectors(conn: sqlite3.Connection, selected: set[str]) -> list[Sector]:
    sql = """
        SELECT sector_name, sector_code, source
        FROM sector_list
        WHERE sector_type = 'Industry'
          AND sector_code IS NOT NULL
          AND TRIM(sector_code) <> ''
          AND source LIKE 'eastmoney%'
        ORDER BY sector_name
    """
    rows = conn.execute(sql).fetchall()
    sectors = [
        Sector(str(row["sector_name"]), str(row["sector_code"]), str(row["source"] or "eastmoney"))
        for row in rows
    ]
    if selected:
        sectors = [sector for sector in sectors if sector.name in selected]
    return sectors


def latest_sector_date(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        f"SELECT MAX(DATE(trade_date)) AS max_date FROM {TARGET_TABLE}"
    ).fetchone()
    if not row or not row["max_date"]:
        raise RuntimeError("sector_daily_prices has no max trade_date")
    return str(row["max_date"])


def parse_kline(sector: Sector, line: str) -> dict[str, Any] | None:
    parts = line.split(",")
    if len(parts) < 9:
        return None
    try:
        return {
            "sector_name": sector.name,
            "sector_code": sector.code,
            "trade_date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            # Eastmoney BK K-line 返回成交量单位为“手”；本地 sector_daily_prices
            # 统一存储为“股”，与 stock_daily_prices 的合成量保持一致。
            "volume": float(parts[5]) * EASTMONEY_VOLUME_MULTIPLIER,
            "amount": float(parts[6]),
            "pct_chg": float(parts[8]),
            "source": sector.source,
            "quality_flag": "ok",
        }
    except ValueError:
        return None


def fetch_sector(
    sector: Sector,
    start_date: str,
    end_date: str,
    retries: int,
    timeout: int,
    request_delay: float,
    gate: EastmoneyRequestGate | None = None,
) -> FetchResult:
    params = {
        "secid": f"90.{sector.code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": start_date,
        "end": end_date,
    }
    active_gate = gate or eastmoney_gate(request_delay=request_delay, timeout=timeout)
    try:
        payload = active_gate.get(
            EASTMONEY_URLS[0],
            params=params,
            headers=HEADERS,
            timeout=timeout,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        klines = data.get("klines") if isinstance(data, dict) else None
        rows = [row for line in (klines or []) if (row := parse_kline(sector, line))]
        if not rows:
            active_gate.record_failure()
            return FetchResult(
                sector.name,
                sector.code,
                False,
                [],
                "empty kline response; no vendor row written",
            )
        rows.sort(key=lambda item: item["trade_date"])
        return FetchResult(sector.name, sector.code, True, rows)
    except (EastmoneyCircuitOpen, EastmoneyBudgetExceeded, EastmoneyRequestError) as exc:
        return FetchResult(
            sector.name,
            sector.code,
            False,
            [],
            f"Eastmoney gate failure: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 - preserve source error in report.
        return FetchResult(sector.name, sector.code, False, [], str(exc))


def load_existing_sector_rows(
    conn: sqlite3.Connection,
    sector_name: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            sector_name,
            sector_code,
            DATE(trade_date) AS trade_date,
            open,
            close,
            high,
            low,
            volume,
            amount,
            pct_chg
            ,source
            ,quality_flag
        FROM {TARGET_TABLE}
        WHERE sector_name = ?
          AND DATE(trade_date) BETWEEN DATE(?) AND DATE(?)
        ORDER BY DATE(trade_date)
        """,
        (sector_name, date_text(start_date), date_text(end_date)),
    ).fetchall()
    return [
        {
            "sector_name": str(row["sector_name"]),
            "sector_code": str(row["sector_code"] or ""),
            "trade_date": str(row["trade_date"]),
            "open": float(row["open"] or 0),
            "close": float(row["close"] or 0),
            "high": float(row["high"] or 0),
            "low": float(row["low"] or 0),
            "volume": float(row["volume"] or 0),
            "amount": float(row["amount"] or 0),
            "pct_chg": float(row["pct_chg"] or 0),
            "source": str(row["source"] or ""),
            "quality_flag": str(row["quality_flag"] or ""),
        }
        for row in rows
    ]


def find_rescale_anchor(rows: list[dict[str, Any]]) -> tuple[int | None, float | None]:
    for index in range(1, len(rows)):
        previous = rows[index - 1]
        current = rows[index]
        prev_close = float(previous["close"])
        close = float(current["close"])
        if prev_close <= 0 or close <= 0:
            continue
        ratio = close / prev_close
        if 0.5 <= ratio <= 2:
            continue
        pct_base = 1 + (float(current["pct_chg"]) / 100)
        if abs(pct_base) < 1e-6:
            continue
        implied_prev_close = close / pct_base
        if implied_prev_close <= 0:
            continue
        factor = implied_prev_close / prev_close
        if not math.isfinite(factor) or factor <= 0:
            continue
        return index, factor
    return None, None


def rescale_legacy_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    repaired_rows = [dict(row) for row in rows]
    last_anchor_index: int | None = None

    for index in range(1, len(repaired_rows)):
        previous = repaired_rows[index - 1]
        current = repaired_rows[index]
        prev_close = float(previous["close"])
        close = float(current["close"])
        if prev_close <= 0 or close <= 0:
            continue
        ratio = close / prev_close
        if 0.5 <= ratio <= 2:
            continue
        pct_base = 1 + (float(current["pct_chg"]) / 100)
        if abs(pct_base) < 1e-6:
            continue
        implied_prev_close = close / pct_base
        factor = implied_prev_close / prev_close
        if not math.isfinite(factor) or factor <= 0:
            continue
        for row_to_scale in repaired_rows[:index]:
            for key in ("open", "close", "high", "low"):
                row_to_scale[key] = round(float(row_to_scale[key]) * factor, 4)
        last_anchor_index = index

    if last_anchor_index is None:
        for row in repaired_rows:
            row["source"] = "legacy_existing"
            row["quality_flag"] = "fallback_no_anchor"
        sync_pct_chg_from_close(repaired_rows)
        return repaired_rows, "fallback_no_anchor"

    for index, row in enumerate(repaired_rows):
        if index < last_anchor_index:
            row["source"] = "synthetic_rescaled"
            row["quality_flag"] = "fallback_rescaled"
        else:
            row["source"] = "legacy_existing_after_anchor"
            row["quality_flag"] = "fallback_existing_after_anchor"
    sync_pct_chg_from_close(repaired_rows)
    return repaired_rows, "fallback_rescaled"


def sync_pct_chg_from_close(rows: list[dict[str, Any]]) -> None:
    for index in range(1, len(rows)):
        previous_close = float(rows[index - 1]["close"])
        close = float(rows[index]["close"])
        if previous_close <= 0:
            continue
        rows[index]["pct_chg"] = round((close / previous_close - 1) * 100, 4)


def fill_failed_with_rescaled_fallback(
    results: list[FetchResult],
    start_date: str,
    end_date: str,
) -> list[FetchResult]:
    # Kept as a compatibility entry point for callers that imported the old
    # helper.  Legacy rescaling would manufacture a vendor history and is now
    # deliberately disabled.
    failed = sum(1 for result in results if not result.ok)
    if failed:
        print(
            f"legacy fallback disabled; keeping failed Eastmoney sectors: {failed}",
            flush=True,
        )
    return results


def build_legacy_results(
    sectors: list[Sector],
    start_date: str,
    end_date: str,
) -> list[FetchResult]:
    return [
        FetchResult(
            sector.name,
            sector.code,
            False,
            [],
            "legacy-only repair disabled; vendor rows require Eastmoney",
        )
        for sector in sectors
    ]


def build_scan_results(
    sectors: list[Sector],
    start_date: str,
    end_date: str,
) -> list[FetchResult]:
    results: list[FetchResult] = []
    with connect() as conn:
        for sector in sectors:
            rows = load_existing_sector_rows(conn, sector.name, start_date, end_date)
            if not rows:
                results.append(FetchResult(sector.name, sector.code, False, [], "no legacy rows"))
                continue
            results.append(
                FetchResult(
                    sector_name=sector.name,
                    sector_code=sector.code,
                    ok=True,
                    rows=rows,
                    error=f"scan existing {TARGET_TABLE}",
                )
            )
    return results


def is_synthetic_like(row: dict[str, Any]) -> bool:
    high = round(float(row["high"]), 4)
    low = round(float(row["low"]), 4)
    open_ = round(float(row["open"]), 4)
    close = round(float(row["close"]), 4)
    return high == max(open_, close) and low == min(open_, close)


def quality_report(results: list[FetchResult]) -> dict[str, Any]:
    rows = [row for result in results if result.ok for row in result.rows]
    rows_by_sector: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_sector.setdefault(row["sector_name"], []).append(row)

    invalid_ohlc = []
    pct_mismatch = []
    huge_jumps = []
    synthetic_like_rows = 0
    non_vendor_rows = 0
    synthetic_source_rows = 0
    for sector_name, sector_rows in rows_by_sector.items():
        sector_rows.sort(key=lambda item: item["trade_date"])
        previous = None
        for row in sector_rows:
            source = str(row.get("source") or "")
            quality_flag = str(row.get("quality_flag") or "")
            if not source.startswith("eastmoney"):
                non_vendor_rows += 1
            if source.startswith("synthetic") or "synthetic" in quality_flag or "fallback" in quality_flag:
                synthetic_source_rows += 1
            if row["high"] < max(row["open"], row["close"]) or row["low"] > min(row["open"], row["close"]):
                invalid_ohlc.append({k: row[k] for k in ("sector_name", "trade_date", "open", "high", "low", "close")})
            if is_synthetic_like(row):
                synthetic_like_rows += 1
            if previous and previous["close"] > 0:
                ratio = row["close"] / previous["close"]
                implied_pct = (ratio - 1) * 100
                pct_diff = abs(implied_pct - row["pct_chg"])
                if pct_diff > 0.45:
                    pct_mismatch.append({
                        "sector_name": sector_name,
                        "trade_date": row["trade_date"],
                        "pct_chg": row["pct_chg"],
                        "implied_pct": round(implied_pct, 4),
                        "pct_diff": round(pct_diff, 4),
                    })
                if ratio > 2 or ratio < 0.5:
                    huge_jumps.append({
                        "sector_name": sector_name,
                        "trade_date": row["trade_date"],
                        "prev_close": previous["close"],
                        "close": row["close"],
                        "ratio": round(ratio, 4),
                    })
            previous = row

    failures = [asdict(result) | {"rows": len(result.rows)} for result in results if not result.ok]
    fallback_results = [
        result
        for result in results
        if result.ok
        and any(str(row.get("quality_flag", "")).startswith("fallback") for row in result.rows)
    ]
    fallback_rows = [
        row
        for result in fallback_results
        for row in result.rows
        if str(row.get("quality_flag", "")).startswith("fallback")
    ]
    counts = [len(sector_rows) for sector_rows in rows_by_sector.values()]
    dates = sorted({row["trade_date"] for row in rows})
    return {
        "database": str(DB_PATH),
        "sectors_requested": len(results),
        "sectors_ok": sum(1 for result in results if result.ok),
        "sectors_failed": len(failures),
        "rows": len(rows),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "min_rows_per_sector": min(counts) if counts else 0,
        "max_rows_per_sector": max(counts) if counts else 0,
        "invalid_ohlc_count": len(invalid_ohlc),
        "pct_mismatch_count": len(pct_mismatch),
        "huge_jump_count": len(huge_jumps),
        "synthetic_like_rows": synthetic_like_rows,
        "non_vendor_rows": non_vendor_rows,
        "synthetic_source_rows": synthetic_source_rows,
        "fallback_sector_count": len(fallback_results),
        "fallback_row_count": len(fallback_rows),
        "fallback_sector_sample": [
            {
                "sector_name": result.sector_name,
                "sector_code": result.sector_code,
                "rows": len(result.rows),
                "note": result.error,
            }
            for result in fallback_results[:20]
        ],
        "failures": failures[:50],
        "invalid_ohlc_sample": invalid_ohlc[:20],
        "pct_mismatch_sample": pct_mismatch[:20],
        "huge_jump_sample": huge_jumps[:20],
    }


def ensure_target_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute(f"PRAGMA table_info({TARGET_TABLE})").fetchall()
    }
    for column, column_type in {
        "sector_code": "TEXT",
        "source": "TEXT",
        "quality_flag": "TEXT",
        "updated_at": "TEXT",
    }.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE {TARGET_TABLE} ADD COLUMN {column} {column_type}")


def backup_database(conn: sqlite3.Connection) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"local_quant.sector_kline_repair_{stamp}.db"
    with sqlite3.connect(backup_path) as backup_conn:
        conn.backup(backup_conn)
    return backup_path


def normalize_legacy_volume_units(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> int:
    """把可由成分股总量确认的历史“手”统一为“股”。

    行业合成量和 stock_daily_prices 都以股存储；当已有板块量仅为成分股总量
    的约 1/100 时，说明旧 Eastmoney 行把“手”直接写入了统一表。只处理这个
    窄比例区间，无法确认单位的行保持原值，避免把异常行业量误放大。
    """
    if not rows:
        return 0
    sector_names = sorted({str(row["sector_name"]) for row in rows})
    start_date = min(str(row["trade_date"])[:10] for row in rows)
    end_date = max(str(row["trade_date"])[:10] for row in rows)
    placeholders = ",".join("?" for _ in sector_names)
    component_rows = conn.execute(
        f"""
        SELECT m.sector_name, DATE(k.trade_date) AS trade_date, SUM(k.volume) AS volume
        FROM stock_sector_map m
        JOIN stock_daily_prices k ON k.symbol = m.symbol
        WHERE m.sector_name IN ({placeholders})
          AND DATE(k.trade_date) BETWEEN DATE(?) AND DATE(?)
        GROUP BY m.sector_name, DATE(k.trade_date)
        """,
        (*sector_names, start_date, end_date),
    ).fetchall()
    component_by_key = {
        (str(row["sector_name"]), str(row["trade_date"])): float(row["volume"] or 0)
        for row in component_rows
    }

    updated = 0
    for row in rows:
        current_volume = float(row.get("volume") or 0)
        component_volume = component_by_key.get(
            (str(row["sector_name"]), str(row["trade_date"])[:10]),
            0,
        )
        if current_volume <= 0 or component_volume <= 0:
            continue
        ratio = current_volume / component_volume
        if not 0.005 <= ratio <= 0.02:
            continue
        row["volume"] = round(current_volume * EASTMONEY_VOLUME_MULTIPLIER, 4)
        flag = str(row.get("quality_flag") or "repaired")
        if "volume_shares" not in flag:
            row["quality_flag"] = f"{flag}|volume_shares"
        updated += 1
    return updated


def write_report(report: dict[str, Any], apply: bool) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "apply" if apply else "dry_run"
    report_path = REPORT_DIR / f"sector_kline_repair_{mode}_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def apply_repair(
    conn: sqlite3.Connection,
    sectors: list[Sector],
    results: list[FetchResult],
) -> dict[str, Any]:
    rows = [row for result in results if result.ok for row in result.rows]
    invalid_rows = [
        row
        for row in rows
        if not str(row.get("source") or "").startswith("eastmoney")
        or "synthetic" in str(row.get("quality_flag") or "")
        or "fallback" in str(row.get("quality_flag") or "")
    ]
    if invalid_rows:
        raise RuntimeError(
            "refusing to apply non-Eastmoney/synthetic rows to vendor table: "
            f"{len(invalid_rows)}"
        )
    volume_rescaled_rows = normalize_legacy_volume_units(conn, rows)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        row["updated_at"] = now

    ensure_target_columns(conn)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE}_rebuild (
            sector_name TEXT,
            sector_code TEXT,
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            source TEXT,
            quality_flag TEXT,
            updated_at TEXT,
            PRIMARY KEY (sector_name, trade_date)
        )
        """
    )
    conn.execute(f"DELETE FROM {TARGET_TABLE}_rebuild")
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO {TARGET_TABLE}_rebuild (
            sector_name, sector_code, trade_date, open, close, high, low, volume, amount,
            pct_chg, source, quality_flag, updated_at
        ) VALUES (
            :sector_name, :sector_code, :trade_date, :open, :close, :high, :low, :volume,
            :amount, :pct_chg, :source, :quality_flag, :updated_at
        )
        """,
        rows,
    )
    conn.execute(
        f"""
        DELETE FROM {TARGET_TABLE}
        WHERE sector_name IN (SELECT sector_name FROM {TARGET_TABLE}_rebuild)
        """
    )
    conn.execute(
        f"""
        INSERT INTO {TARGET_TABLE} (
            sector_name, sector_code, trade_date, open, close, high, low, volume, amount,
            pct_chg, source, quality_flag, updated_at
        )
        SELECT sector_name, sector_code, trade_date, open, close, high, low, volume, amount,
               pct_chg, source, quality_flag, updated_at
        FROM {TARGET_TABLE}_rebuild
        """
    )
    return {
        "sectors_replaced": len({sector.name for sector in sectors}),
        "rows_inserted": len(rows),
        "volume_rescaled_rows": volume_rescaled_rows,
    }


def print_summary(report: dict[str, Any], report_path: Path, apply: bool) -> None:
    mode = str(report.get("mode") or ("APPLY" if apply else "DRY-RUN")).upper()
    print(f"\n[{mode}] sector kline repair summary", flush=True)
    for key in [
        "sectors_requested",
        "sectors_ok",
        "sectors_failed",
        "rows",
        "date_min",
        "date_max",
        "min_rows_per_sector",
        "max_rows_per_sector",
        "invalid_ohlc_count",
        "pct_mismatch_count",
        "huge_jump_count",
        "synthetic_like_rows",
        "non_vendor_rows",
        "synthetic_source_rows",
        "fallback_sector_count",
        "fallback_row_count",
    ]:
        print(f"{key}: {report.get(key)}", flush=True)
    if report.get("backup_path"):
        print(f"backup_path: {report['backup_path']}", flush=True)
    if report.get("apply_result"):
        print(f"apply_result: {report['apply_result']}", flush=True)
    print(f"report_path: {report_path}", flush=True)


def report_has_hard_failures(report: dict[str, Any], max_failures: int) -> bool:
    return any(
        [
            int(report.get("sectors_failed") or 0) > max_failures,
            int(report.get("invalid_ohlc_count") or 0) > 0,
            int(report.get("pct_mismatch_count") or 0) > 0,
            int(report.get("huge_jump_count") or 0) > 0,
            int(report.get("non_vendor_rows") or 0) > 0,
            int(report.get("synthetic_source_rows") or 0) > 0,
        ]
    )


def retry_failed_sectors(
    sectors: list[Sector],
    results: list[FetchResult],
    start: str,
    end: str,
    retries: int,
    timeout: int,
    request_delay: float,
    rounds: int,
) -> list[FetchResult]:
    # The normal gate already performs one reconnect retry.  Re-running failed
    # sectors in this repair script used to create a request storm and defeat
    # the circuit breaker, so the compatibility function now leaves results
    # unchanged.  A later scheduled retry should invoke the same bounded
    # workflow with a fresh target date.
    if any(not result.ok for result in results):
        print("additional failed-sector rounds disabled by Eastmoney gate", flush=True)
    return results


def main() -> int:
    args = parse_args()
    if args.scan_only and args.apply:
        raise RuntimeError("--scan-only cannot be combined with --apply")
    if args.legacy_only:
        raise RuntimeError(
            "--legacy-only is disabled: sector_vendor_daily only accepts verified Eastmoney rows"
        )
    if args.workers != 1:
        print("workers ignored; Eastmoney requests are serialized", flush=True)
    start = date_compact(args.start_date)
    selected = set(args.sector or [])
    with connect() as conn:
        end = date_compact(args.end_date or latest_sector_date(conn))
        sectors = load_sectors(conn, selected)
        if not sectors:
            raise RuntimeError("No eastmoney Industry sectors found in sector_list")

    started = time.time()
    effective_apply = bool(args.apply)

    if args.scan_only or args.repair_if_needed:
        print(
            f"scanning {len(sectors)} existing sectors, "
            f"range={date_text(start)}..{date_text(end)}",
            flush=True,
        )
        scan_results = build_scan_results(sectors, start, end)
        scan_report = quality_report(scan_results)
        scan_report["elapsed_seconds"] = round(time.time() - started, 2)
        scan_report["apply"] = False
        scan_report["mode"] = "scan"
        scan_report["start_date"] = date_text(start)
        scan_report["end_date"] = date_text(end)
        scan_report_path = write_report(scan_report, False)
        print_summary(scan_report, scan_report_path, False)
        scan_failed = report_has_hard_failures(scan_report, args.max_failures)
        if args.scan_only:
            return 2 if scan_failed else 0
        if args.repair_if_needed:
            if scan_failed:
                print(
                    "scan found hard issues; legacy repair is disabled; no rows changed",
                    flush=True,
                )
            return 2 if scan_failed else 0
        if not scan_failed:
            return 0
        print(
            "scan found hard issues; no legacy repair is performed; "
            "fetching a fresh Eastmoney vendor batch",
            flush=True,
        )

    print(
        f"fetching {len(sectors)} sectors from Eastmoney, "
        f"range={date_text(start)}..{date_text(end)}, workers=1",
        flush=True,
    )
    results = []
    gate = eastmoney_gate(request_delay=args.request_delay, timeout=args.timeout)
    for index, sector in enumerate(sectors, 1):
        result = fetch_sector(
            sector,
            start,
            end,
            args.retries,
            args.timeout,
            args.request_delay,
            gate=gate,
        )
        results.append(result)
        ok = sum(1 for item in results if item.ok)
        status = "ok" if result.ok else "failed"
        print(
            f"  fetched {index}/{len(sectors)} {result.sector_name} {status}; "
            f"ok={ok}, failed={index - ok}",
            flush=True,
        )

    report = quality_report(results)
    report["elapsed_seconds"] = round(time.time() - started, 2)
    report["apply"] = effective_apply
    if args.repair_if_needed:
        report["mode"] = "repair_if_needed"
    report["start_date"] = date_text(start)
    report["end_date"] = date_text(end)

    hard_failures = report_has_hard_failures(report, args.max_failures)

    if effective_apply and hard_failures:
        report["apply_aborted_reason"] = (
            "quality gate failed: failures/invalid_ohlc/pct_mismatch/huge_jumps"
        )
        report_path = write_report(report, effective_apply)
        print_summary(report, report_path, effective_apply)
        return 2

    if effective_apply:
        with connect() as conn:
            backup_path = backup_database(conn)
            with conn:
                apply_result = apply_repair(conn, sectors, results)
            report["backup_path"] = str(backup_path)
            report["apply_result"] = apply_result

    report_path = write_report(report, effective_apply)
    print_summary(report, report_path, effective_apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
