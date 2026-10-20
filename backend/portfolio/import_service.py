"""Preview and atomically book confirmed screenshot rows without changing capital."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from portfolio.accounting import (
    IdempotencyConflictError, InvalidTradeError, PortfolioAccountingService,
    StaleTradePreviewError,
)
from portfolio.import_schema import ensure_import_schema
from portfolio.schema import create_portfolio_tables, _create_positions


logger = logging.getLogger(__name__)
MAX_ROWS = 200
DRAFT_TTL = 86400


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _integer(value: object, label: str, minimum: int = 0, maximum: int = 10**12) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidTradeError(f"{label}必须是{minimum}至{maximum}范围的整数")
    return value


def normalize_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Reject incomplete OCR rather than inventing dates, times or fees."""
    if not 1 <= len(rows) <= MAX_ROWS:
        raise InvalidTradeError(f"每批需要1至{MAX_ROWS}条记录")
    normalized = []
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    for index, row in enumerate(rows):
        side = row.get("business_type")
        if side not in {"BUY", "SELL", "DEPOSIT", "WITHDRAWAL"}:
            raise InvalidTradeError(f"第{index + 1}行交易类型不支持")
        day, clock = row.get("trade_date"), row.get("trade_time")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise InvalidTradeError(f"第{index + 1}行需要完整成交日期")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError as exc:
            raise InvalidTradeError("成交日期无效") from exc
        if day > today:
            raise InvalidTradeError("不能记录未来日期的成交")
        if not isinstance(clock, str) or not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", clock):
            raise InvalidTradeError(f"第{index + 1}行需要实际时间，不自动填入15:00")
        full_clock = clock if len(clock) == 8 else clock + ":00"
        try:
            datetime.strptime(full_clock, "%H:%M:%S")
        except ValueError as exc:
            raise InvalidTradeError("成交时间无效") from exc
        result: dict[str, object] = {
            "row_id": str(row.get("row_id", index))[:100],
            "business_type": side, "trade_date": day, "trade_time": full_clock,
            "occurred_at": f"{day}T{full_clock}+08:00",
            "duplicate_confirmed": row.get("duplicate_confirmed") is True,
            "image_index": row.get("image_index"),
        }
        if side in {"BUY", "SELL"}:
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol):
                raise InvalidTradeError(f"第{index + 1}行需要6位股票代码")
            price = _integer(row.get("price_micros"), "价格", 1, 10_000_000_000)
            qty = _integer(row.get("quantity"), "股数", 1, 100_000_000)
            fee = _integer(row.get("fee_cents"), "税费合计", 0, 10**9)
            tax = _integer(row.get("tax_cents", 0), "单列税费", 0, 10**9)
            gross = PortfolioAccountingService._gross_amount(price, qty)
            net = -(gross + fee + tax) if side == "BUY" else gross - fee - tax
            if row.get("net_cash_cents") is not None and row["net_cash_cents"] != net:
                raise InvalidTradeError(f"第{index + 1}行价格×股数、税费与交割金额不一致")
            result.update(symbol=symbol, price_micros=price, quantity=qty,
                          fee_cents=fee, tax_cents=tax, net_cash_cents=net)
        else:
            amount = _integer(row.get("amount_cents"), "转账金额", 1, 10**12)
            result.update(amount_cents=amount, fee_cents=0, tax_cents=0,
                          net_cash_cents=amount if side == "DEPOSIT" else -amount)
        normalized.append(result)
    return sorted(normalized, key=lambda r: (str(r["occurred_at"]), str(r["row_id"])))


class PortfolioImportService:
    """All ledger mutations share one SQLite transaction and native accounting checks."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.accounting = PortfolioAccountingService(db_path)

    def _rows(self, conn: sqlite3.Connection, account_id: int) -> list[dict[str, object]]:
        return [dict(row) for row in conn.execute(
            "SELECT symbol,quantity FROM portfolio_positions WHERE account_id=? AND quantity>0 "
            "ORDER BY symbol", (account_id,),
        )]

    def _duplicates(self, conn: sqlite3.Connection, account_id: int,
                    rows: list[dict[str, object]], replaced: list[int]) -> bool:
        needs_confirmation = False
        seen: dict[str, int] = {}
        for index, row in enumerate(rows):
            candidates = []
            if row["business_type"] in {"BUY", "SELL"}:
                found = conn.execute("""
                    SELECT t.id AS transaction_id,t.business_type,t.trade_date,t.occurred_at,
                           t.symbol,t.quantity,t.price_micros,t.fee_cents,t.tax_cents
                    FROM portfolio_transactions t
                    WHERE t.account_id=? AND t.business_type=? AND t.trade_date=?
                      AND t.symbol=? AND t.quantity=? AND t.price_micros=?
                      AND NOT EXISTS(SELECT 1 FROM portfolio_transactions r
                                     WHERE r.reversal_of=t.id)
                    ORDER BY t.id LIMIT 50
                """, (account_id, row["business_type"], row["trade_date"], row["symbol"],
                      row["quantity"], row["price_micros"]))
            else:
                found = conn.execute("""
                    SELECT t.id AS transaction_id,t.business_type,t.trade_date,t.occurred_at,
                           t.gross_amount_cents AS amount_cents
                    FROM portfolio_transactions t
                    WHERE t.account_id=? AND t.business_type=? AND t.trade_date=?
                      AND t.gross_amount_cents=?
                      AND NOT EXISTS(SELECT 1 FROM portfolio_transactions r
                                     WHERE r.reversal_of=t.id)
                    ORDER BY t.id LIMIT 50
                """, (account_id, row["business_type"], row["trade_date"], row["amount_cents"]))
            candidates = [dict(r) for r in found if int(r["transaction_id"]) not in replaced]
            signature = _json({k: row.get(k) for k in (
                "business_type", "trade_date", "trade_time", "symbol", "quantity",
                "price_micros", "amount_cents",
            )})
            if signature in seen:
                candidates.append({"batch_row_index": seen[signature],
                                   "reason": "本批存在相同时间、价格和数量，请核对是否独立成交"})
            seen[signature] = index
            row["duplicate_candidates"] = candidates
            if candidates and not row["duplicate_confirmed"]:
                needs_confirmation = True
        return needs_confirmation

    def _simulate_connection(self, source: sqlite3.Connection, account_id: int
                             ) -> sqlite3.Connection:
        simulation = sqlite3.connect(":memory:")
        simulation.row_factory = sqlite3.Row
        try:
            create_portfolio_tables(simulation)
            _create_positions(simulation)
            # Transfers may reference a counterparty. Copy accounts but only this account's ledger.
            accounts = source.execute("SELECT * FROM portfolio_accounts LIMIT 501").fetchall()
            if len(accounts) > 500:
                raise InvalidTradeError("账户数超过本地批量预览上限")
            for row in accounts:
                simulation.execute("INSERT INTO portfolio_accounts VALUES (" +
                                   ",".join("?" for _ in row) + ")", tuple(row))
            ledger = source.execute("""
                SELECT * FROM portfolio_transactions WHERE account_id=? OR counterparty_account_id=?
                OR reversal_of IN (SELECT id FROM portfolio_transactions
                                   WHERE account_id=? OR counterparty_account_id=?)
                ORDER BY id LIMIT 20001
            """, (account_id, account_id, account_id, account_id)).fetchall()
            if len(ledger) > 20000:
                raise InvalidTradeError("账户流水超过批量预览上限，请分账户处理")
            for row in ledger:
                simulation.execute("INSERT INTO portfolio_transactions VALUES (" +
                                   ",".join("?" for _ in row) + ")", tuple(row))
            return simulation
        except Exception:
            simulation.close()
            raise

    def _apply(self, conn: sqlite3.Connection, account_id: int,
               rows: list[dict[str, object]], replaced: list[int], batch_id: str,
               hashes: list[str]) -> tuple[list[int], str]:
        service = self.accounting
        account = service._get_account(conn, account_id)
        service._require_active(account)
        affected_date = min(str(r["trade_date"]) for r in rows)
        for transaction_id in replaced:
            old = conn.execute("SELECT * FROM portfolio_transactions WHERE id=? AND account_id=?",
                               (transaction_id, account_id)).fetchone()
            if old is None or old["business_type"] not in {"BUY", "SELL", "DEPOSIT", "WITHDRAWAL"}:
                raise InvalidTradeError("仅可更正本账户的买卖和资金记录")
            if conn.execute("SELECT 1 FROM portfolio_transactions WHERE reversal_of=?",
                            (transaction_id,)).fetchone():
                raise InvalidTradeError("选择的旧记录已经冲正，请重新预览")
            affected_date = min(affected_date, old["trade_date"])
            conn.execute("""
                INSERT INTO portfolio_transactions(account_id,client_request_id,business_type,
                  status,trade_date,occurred_at,cash_delta_cents,source,reversal_of,note,metadata_json)
                VALUES(?,?,'REVERSAL','POSTED',?,?,?,'IMPORT',?,?,?)
            """, (account_id, f"{batch_id}:reverse:{transaction_id}", old["trade_date"],
                  old["occurred_at"], -old["cash_delta_cents"], transaction_id,
                  "截图批次更正：冲正旧记录", _json({"import_batch_id": batch_id})))
        ids = []
        for index, row in enumerate(rows):
            side = str(row["business_type"])
            values = dict(row)
            if side in {"BUY", "SELL"}:
                preview = service._preview_trade(
                    conn, account_id=account_id, business_type=side,
                    trade_date=str(row["trade_date"]), symbol=str(row["symbol"]),
                    price_micros=int(row["price_micros"]), quantity=int(row["quantity"]),
                    amount_cents=None, target_position_bps=None, account_equity_cents=None,
                    fee_cents=int(row["fee_cents"]), tax_cents=int(row["tax_cents"]),
                )
                gross, net = preview.gross_amount_cents, preview.cash_delta_cents
            else:
                gross, net = int(row["amount_cents"]), int(row["net_cash_cents"])
            cursor = conn.execute("""
                INSERT INTO portfolio_transactions(account_id,client_request_id,business_type,
                  status,trade_date,occurred_at,symbol,quantity,price_micros,gross_amount_cents,
                  cash_delta_cents,fee_cents,tax_cents,source,note,metadata_json)
                VALUES(?,?,?,'POSTED',?,?,?,?,?,?,?,?,?,'IMPORT',?,?)
            """, (account_id, f"{batch_id}:{index}", side, row["trade_date"], row["occurred_at"],
                  row.get("symbol"), row.get("quantity"), row.get("price_micros"), gross, net,
                  row["fee_cents"], row["tax_cents"], "截图记账；费用按交割单合计，未推测拆分",
                  _json({"import_batch_id": batch_id, "row": values, "source_hashes": hashes})))
            ids.append(int(cursor.lastrowid))
        latest = service._latest_account_trade_date(conn, account_id)
        service._rebuild_positions(conn, account_id, latest)
        service._validate_cash_sequence(conn, account)
        return ids, affected_date

    def preview(self, *, account_id: int, rows: list[dict[str, object]],
                client_request_id: str, source_hashes: list[str] | None = None,
                replace_transaction_ids: list[int] | None = None) -> dict[str, object]:
        """Simulate on a small in-memory ledger; only an expiring draft is persisted."""
        _integer(account_id, "账户", 1)
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", client_request_id):
            raise InvalidTradeError("批次请求号无效")
        normalized = normalize_rows(rows)
        replaced = sorted(set(replace_transaction_ids or []))
        if len(replaced) > MAX_ROWS:
            raise InvalidTradeError("更正记录过多")
        for value in replaced:
            _integer(value, "旧记录编号", 1)
        hashes = source_hashes or []
        if len(hashes) > 8 or any(not re.fullmatch(r"[a-f0-9]{64}", h) for h in hashes):
            raise InvalidTradeError("截图来源摘要无效")
        payload = {"account_id": account_id, "rows": normalized,
                   "replace_transaction_ids": replaced, "source_hashes": hashes}
        with closing(self.accounting._connect()) as conn:
            ensure_import_schema(conn)
            conn.execute("BEGIN")
            account = self.accounting._get_account(conn, account_id)
            self.accounting._require_active(account)
            revision = self.accounting._account_ledger_revision(conn, account_id)
            cash = self.accounting._cash_balance(conn, account_id)
            before = self._rows(conn, account_id)
            duplicates = self._duplicates(conn, account_id, normalized, replaced)
            if duplicates:
                return {"draft_id": None, "version": 1, "ledger_revision": revision,
                        "rows": normalized, "needs_duplicate_confirmation": True,
                        "cash_before_cents": cash, "cash_after_cents": cash,
                        "positions_before": before, "positions_after": before,
                        "warnings": ["存在疑似重复，请跳过、更正旧记录，或确认是独立成交"],
                        "expires_at": None}
            with closing(self._simulate_connection(conn, account_id)) as sim:
                self._apply(sim, account_id, normalized, replaced, "preview", hashes)
                after_cash = self.accounting._cash_balance(sim, account_id)
                after = self._rows(sim, account_id)
            existing = conn.execute("SELECT * FROM portfolio_import_drafts WHERE request_id=?",
                                    (client_request_id,)).fetchone()
            if existing:
                if existing["payload_json"] != _json(payload):
                    raise IdempotencyConflictError("批次请求号已用于其他内容")
                if existing["ledger_revision"] == revision and existing["expires_at"] > time.time():
                    return json.loads(existing["preview_json"])
                raise StaleTradePreviewError("旧预览已失效，请使用新请求号重新预览")
            now = int(time.time())
            draft_id = uuid.uuid4().hex
            result = {"draft_id": draft_id, "version": 1, "ledger_revision": revision,
                      "cash_before_cents": cash, "cash_after_cents": after_cash,
                      "positions_before": before, "positions_after": after,
                      "rows": normalized, "warnings": [], "expires_at": now + DRAFT_TTL,
                      "needs_duplicate_confirmation": False,
                      "replace_transaction_ids": replaced}
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            if self.accounting._account_ledger_revision(conn, account_id) != revision:
                raise StaleTradePreviewError("预览期间账本已变化，请重新预览")
            conn.execute("""DELETE FROM portfolio_import_drafts WHERE id IN (
                SELECT id FROM portfolio_import_drafts WHERE committed_at IS NULL
                AND expires_at<? LIMIT 100)""", (now,))
            if conn.execute("SELECT count(*) FROM portfolio_import_drafts WHERE account_id=? "
                            "AND committed_at IS NULL", (account_id,)).fetchone()[0] >= 100:
                raise InvalidTradeError("未确认草稿已达上限，请清理草稿或稍后重试")
            conn.execute("""INSERT INTO portfolio_import_drafts(id,account_id,version,
                ledger_revision,request_id,payload_json,preview_json,created_at,expires_at)
                VALUES(?,?,1,?,?,?,?,?,?)""", (draft_id, account_id, revision,
                client_request_id, _json(payload), _json(result), now, now + DRAFT_TTL))
            conn.commit()
            return result

    def commit(self, *, draft_id: str, version: int,
               client_request_id: str) -> dict[str, object]:
        """One short transaction, immutable receipt, safe replay after network loss."""
        if not re.fullmatch(r"[a-f0-9]{32}", draft_id):
            raise InvalidTradeError("草稿编号无效")
        with closing(self.accounting._connect()) as conn:
            ensure_import_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            draft = conn.execute("SELECT * FROM portfolio_import_drafts WHERE id=?",
                                 (draft_id,)).fetchone()
            if draft is None:
                raise InvalidTradeError("草稿不存在或已清理")
            if version != draft["version"] or client_request_id != draft["request_id"]:
                raise IdempotencyConflictError("确认内容与预览不一致")
            if draft["committed_at"] is not None:
                result = json.loads(draft["result_json"])
                result["duplicate_replay"] = True
                return result
            if draft["expires_at"] <= time.time():
                raise StaleTradePreviewError("草稿已过期，请重新预览")
            account_id = int(draft["account_id"])
            if self.accounting._account_ledger_revision(conn, account_id) != draft["ledger_revision"]:
                raise StaleTradePreviewError("账本已变化，请重新预览")
            payload = json.loads(draft["payload_json"])
            ids, affected = self._apply(conn, account_id, payload["rows"],
                                       payload["replace_transaction_ids"], draft_id,
                                       payload["source_hashes"])
            # Invalidate old checkpoints immediately; no stale intraday quote/state may
            # remain usable between booking and the bounded recomputation below.
            affected_symbols = {r.get("symbol") for r in payload["rows"] if r.get("symbol")}
            replaced_ids = payload["replace_transaction_ids"]
            if replaced_ids:
                placeholders = ",".join("?" for _ in replaced_ids)
                old_symbols = conn.execute(
                    "SELECT DISTINCT symbol FROM portfolio_transactions "
                    f"WHERE account_id=? AND id IN ({placeholders}) AND symbol IS NOT NULL",
                    (account_id, *replaced_ids),
                ).fetchall()
                affected_symbols.update(row["symbol"] for row in old_symbols)
            for symbol in affected_symbols:
                conn.execute("DELETE FROM portfolio_exit_states WHERE account_id=? AND symbol=?",
                             (account_id, symbol))
            from portfolio.exit_engine import PortfolioExitEngine
            exit_engine = PortfolioExitEngine(self.db_path)
            plan_rows = conn.execute("SELECT * FROM portfolio_exit_plans WHERE account_id=?",
                                     (account_id,)).fetchall()
            for plan in plan_rows:
                if not exit_engine._plan_entry_is_current(conn, plan):
                    exit_engine._resolve_plan_events(conn, account_id, plan["symbol"],
                        plan["position_revision"], datetime.now(ZoneInfo("UTC")).isoformat())
                    conn.execute("UPDATE portfolio_exit_plans SET enabled=0,updated_at=CURRENT_TIMESTAMP "
                        "WHERE account_id=? AND symbol=? AND position_revision=?",
                        (account_id, plan["symbol"], plan["position_revision"]))
            # Old snapshots must never be displayed as if they included this batch.
            latest_snapshot = conn.execute("SELECT max(snapshot_date) FROM "
                "portfolio_account_daily_snapshots WHERE account_id=?", (account_id,)).fetchone()[0]
            for table in ("portfolio_position_daily_snapshots", "portfolio_account_daily_snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE account_id=? AND snapshot_date>=?",
                             (account_id, affected))
            result = {"booked_count": len(ids), "transaction_ids": ids,
                      "cash_after_cents": self.accounting._cash_balance(conn, account_id),
                      "duplicate_replay": False, "performance_status": "PENDING",
                      "account_id": account_id, "affected_date": affected,
                      "previous_snapshot_end": latest_snapshot,
                      "reversed_count": len(payload["replace_transaction_ids"])}
            conn.execute("UPDATE portfolio_import_drafts SET committed_at=?,result_json=? WHERE id=?",
                         (int(time.time()), _json(result), draft_id))
            conn.commit()
        return result

    def refresh_after_commit(self, draft_id: str) -> dict[str, object]:
        """Bounded derived-data refresh. Failure keeps an explicit pending receipt."""
        from portfolio.performance import PortfolioPerformanceService
        from portfolio.exit_engine import PortfolioExitEngine

        class SilentExitEngine(PortfolioExitEngine):
            def _insert_event(self, *args: object, **kwargs: object) -> None:
                return None

        with closing(self.accounting._connect()) as conn:
            draft = conn.execute("SELECT result_json FROM portfolio_import_drafts WHERE id=?",
                                 (draft_id,)).fetchone()
            if draft is None or draft[0] is None:
                raise InvalidTradeError("未找到已提交批次")
            result = json.loads(draft[0])
            if result["performance_status"] == "COMPLETE":
                return result
            account_id = result["account_id"]
            try:
                dates = conn.execute("SELECT DISTINCT trade_date FROM stock_daily_prices "
                    "WHERE trade_date>=? ORDER BY trade_date LIMIT 401",
                    (result["affected_date"],)).fetchall()
                end = conn.execute("SELECT max(trade_date) FROM stock_daily_prices").fetchone()[0]
                start = dates[0][0] if dates and len(dates) <= 400 else None
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                return result
        try:
            if start and end and start <= end:
                PortfolioPerformanceService(self.db_path).rebuild_account_snapshots(account_id, start, end)
                result["performance_status"] = "COMPLETE"
        except (ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
            logger.warning("Portfolio import derived refresh pending: %s", type(exc).__name__)
            result["performance_status"] = "PENDING"
        result["monitoring_status"] = "PENDING"
        if end:
            try:
                run = SilentExitEngine(self.db_path).run_daily(end, account_id=account_id)
                if all(item.blocked_positions == 0 for item in run.accounts):
                    result["monitoring_status"] = "COMPLETE"
            except (ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
                logger.warning("Portfolio import monitoring refresh pending: %s", type(exc).__name__)
        with closing(self.accounting._connect()) as conn:
            conn.execute("UPDATE portfolio_import_drafts SET result_json=? WHERE id=?",
                         (_json(result), draft_id))
            conn.commit()
        return result
