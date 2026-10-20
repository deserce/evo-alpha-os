"""Persisted and retryable email delivery for Watch Engine events."""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from app.core.config import settings
from app.notifications.email_notifier import EmailDeliveryResult, send_notification
from quant_engine.watch.engine import DB_PATH, WatchEngine


logger = logging.getLogger(__name__)
URGENT_EVENT_TYPES = ("TRIGGERED", "INVALIDATED", "DATA_BLOCKED")
Sender = Callable[..., EmailDeliveryResult]


class WatchNotificationDispatcher:
    def __init__(self, db_path: str | Path | None = None, *, sender: Sender = send_notification):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.sender = sender

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        WatchEngine(self.db_path).ensure_schema(conn)
        return conn

    @staticmethod
    def _stats() -> dict[str, int]:
        return {"sent": 0, "failed": 0, "skipped": 0}

    def _ensure_delivery(
        self,
        conn: sqlite3.Connection,
        *,
        dedupe_key: str,
        event_id: str | None,
        delivery_type: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM notification_deliveries WHERE dedupe_key=?",
            (dedupe_key,),
        ).fetchone()
        if row:
            return row
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO notification_deliveries (
                delivery_id, event_id, channel, delivery_type, dedupe_key,
                status, attempt_count, created_at, updated_at
            ) VALUES (?, ?, 'email', ?, ?, 'PENDING', 0, ?, ?)
            """,
            (uuid4().hex, event_id, delivery_type, dedupe_key, now, now),
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM notification_deliveries WHERE dedupe_key=?",
            (dedupe_key,),
        ).fetchone()

    def _send(
        self,
        conn: sqlite3.Connection,
        delivery: sqlite3.Row,
        subject: str,
        body: str,
    ) -> str | None:
        if delivery["status"] in {"SENT", "SKIPPED"}:
            return None
        result = self.sender(subject, body, is_html=True)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        attempts = int(delivery["attempt_count"] or 0) + 1
        if result.success:
            status = "SENT"
            last_error = None
            sent_at = now
            outcome = "sent"
        elif result.skipped:
            # Keep the delivery pending so enabling/fixing SMTP later can send it.
            status = "PENDING"
            attempts = int(delivery["attempt_count"] or 0)
            last_error = result.error or "email delivery skipped"
            sent_at = None
            outcome = "skipped"
        else:
            status = "FAILED"
            last_error = "email delivery failed"
            sent_at = None
            outcome = "failed"
        conn.execute(
            """
            UPDATE notification_deliveries
            SET status=?, attempt_count=?, last_error=?, sent_at=?, updated_at=?
            WHERE delivery_id=?
            """,
            (status, attempts, last_error, sent_at, now, delivery["delivery_id"]),
        )
        conn.commit()
        return outcome

    @staticmethod
    def _json_mapping(value: object) -> dict:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _price(value: object) -> str:
        if value is None or value == "":
            return "—"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return html.escape(str(value))
        return f"{number:.2f}"

    @staticmethod
    def _state_meta(state: str) -> tuple[str, str, str]:
        return {
            "TRIGGERED": ("确认触发", "#047857", "#d1fae5"),
            "IN_ZONE": ("进入回调", "#b45309", "#fef3c7"),
            "INVALIDATED": ("已经失效", "#be123c", "#ffe4e6"),
            "UNKNOWN": ("数据异常", "#1d4ed8", "#dbeafe"),
            "WATCHING": ("持续观察", "#475569", "#e2e8f0"),
            "DATA_BLOCKED": ("数据阻断", "#be123c", "#ffe4e6"),
            "DATA_RECOVERED": ("数据恢复", "#047857", "#d1fae5"),
        }.get(str(state or "").upper(), (str(state or "状态"), "#475569", "#e2e8f0"))

    @staticmethod
    def _path_meta(factors: dict) -> tuple[str, str]:
        path = str(factors.get("setup_path") or "").upper()
        if path == "TREND_PULLBACK":
            return "路径 A · 强势回调", "#0369a1"
        if path == "REPAIR_REVERSAL":
            return "路径 B · 领先修复", "#b45309"
        return "路径待确认", "#64748b"

    @classmethod
    def _strategy_note(cls, payload: dict, *, digest: bool = False) -> str:
        path = cls._path_meta(payload)[0] if payload.get("setup_path") else "路径未记录"
        version = payload.get("rule_version")
        version_label = f"规则 v{version}" if version not in (None, "") else "规则版本未记录"
        strategy = "路径 A · 强势回调 / 路径 B · 领先修复" if digest else path
        source = "盘中已完成 5 分钟 K 线确认" if payload.get("source") == "intraday_5min" else "日线状态计算"
        return (
            '<div style="margin:12px 0;padding:12px 14px;background:#f1f5f9;'
            'border-radius:10px;font-size:12px;line-height:1.8;color:#334155;">'
            f'采用策略：两段式低吸 · {html.escape(strategy)} · {html.escape(version_label)}'
            f'<br>信号来源：{source}。C结构策略独立监控，本邮件不含C信号。</div>'
        )

    @staticmethod
    def _badge(label: str, color: str, background: str) -> str:
        return (
            f'<span style="display:inline-block;padding:4px 9px;border-radius:999px;'
            f'font-size:12px;line-height:1.2;font-weight:700;color:{color};'
            f'background:{background};">{html.escape(label)}</span>'
        )

    @staticmethod
    def _public_link(path: str) -> str:
        if not settings.EVOALPHA_PUBLIC_URL:
            return ""
        return f"{settings.EVOALPHA_PUBLIC_URL.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone() is not None

    @staticmethod
    def _json_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        try:
            parsed = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if str(item).strip()]

    @staticmethod
    def _state_counts(rows: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            state = str(row.get("watch_state") or "WATCHING").upper()
            counts[state] = counts.get(state, 0) + 1
        return counts

    def _pool_catalog(self, conn: sqlite3.Connection) -> dict[str, dict]:
        if not self._table_exists(conn, "stock_pools"):
            return {}
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(stock_pools)")
        }
        name_expression = "name" if "name" in columns else "pool_id"
        sort_expression = "sort_order" if "sort_order" in columns else "0"
        rows = conn.execute(
            f"SELECT pool_id, {name_expression}, {sort_expression} FROM stock_pools"
        ).fetchall()
        return {
            str(row[0]): {
                "pool_id": str(row[0]),
                "pool_name": str(row[1] or row[0]),
                "sort_order": int(row[2] or 0),
            }
            for row in rows
        }

    def _digest_dataset(self, conn: sqlite3.Connection, trade_date: str) -> dict:
        """Use the same latest manual historical scan as the Watchlist result table."""
        scan = None
        if self._table_exists(conn, "watch_scan_runs") and self._table_exists(
            conn, "watch_scan_results"
        ):
            scan = conn.execute(
                """
                SELECT r.scan_id, r.rule_version
                FROM watch_scan_runs r
                WHERE r.status='COMPLETED'
                  AND COALESCE(r.origin, 'MANUAL')='MANUAL'
                  AND EXISTS (
                      SELECT 1 FROM watch_scan_results s
                      WHERE s.scan_id=r.scan_id AND s.trade_date=?
                  )
                ORDER BY COALESCE(r.completed_at, r.created_at) DESC,
                         r.created_at DESC
                LIMIT 1
                """,
                (trade_date,),
            ).fetchone()
        if scan:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM watch_scan_results
                    WHERE scan_id=? AND trade_date=?
                    ORDER BY CASE watch_state WHEN 'TRIGGERED' THEN 1
                        WHEN 'IN_ZONE' THEN 2 WHEN 'INVALIDATED' THEN 3
                        WHEN 'UNKNOWN' THEN 4 ELSE 5 END, symbol
                    """,
                    (scan["scan_id"], trade_date),
                ).fetchall()
            ]
            source = "manual_scan"
            scan_id = str(scan["scan_id"])
            rule_version = int(scan["rule_version"])
        else:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM watch_daily_snapshot WHERE trade_date=?
                    ORDER BY CASE watch_state WHEN 'TRIGGERED' THEN 1
                        WHEN 'IN_ZONE' THEN 2 WHEN 'INVALIDATED' THEN 3
                        WHEN 'UNKNOWN' THEN 4 ELSE 5 END, symbol
                    """,
                    (trade_date,),
                ).fetchall()
            ]
            source = "daily_snapshot"
            scan_id = None
            versions = sorted({str(row["rule_version"]) for row in rows
                               if row.get("rule_version") is not None})
            rule_version = " / v".join(versions) if versions else None

        catalog = self._pool_catalog(conn)
        grouped: dict[str, dict] = {}
        for row in rows:
            if source == "manual_scan":
                pool_ids = self._json_list(row.get("pool_ids"))
            else:
                pool_ids = [str(row.get("pool_id"))] if row.get("pool_id") else []
            if not pool_ids:
                pool_ids = ["unassigned"]
            for pool_id in pool_ids:
                pool = catalog.get(
                    pool_id,
                    {
                        "pool_id": pool_id,
                        "pool_name": row.get("pool_name") or "未分组",
                        "sort_order": 9999,
                    },
                )
                group = grouped.setdefault(
                    pool_id,
                    {
                        "pool_id": pool_id,
                        "pool_name": pool["pool_name"],
                        "sort_order": pool["sort_order"],
                        "rows": [],
                    },
                )
                group["rows"].append(row)
        groups = sorted(
            grouped.values(),
            key=lambda group: (group["sort_order"], group["pool_name"]),
        )
        return {
            "source": source,
            "scan_id": scan_id,
            "rule_version": rule_version,
            "rows": rows,
            "groups": groups,
        }

    @classmethod
    def _document(
        cls,
        *,
        eyebrow: str,
        title: str,
        subtitle: str,
        content: str,
        accent: str = "#22d3ee",
    ) -> str:
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title></head>'
            '<body style="margin:0;padding:0;background:#eef2f7;color:#0f172a;'
            'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,'
            'PingFang SC,Hiragino Sans GB,Microsoft YaHei,sans-serif;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="width:100%;background:#eef2f7;"><tr><td align="center" '
            'style="padding:24px 12px;">'
            '<table role="presentation" width="680" cellspacing="0" cellpadding="0" '
            'style="width:100%;max-width:680px;background:#ffffff;border-radius:18px;'
            'overflow:hidden;box-shadow:0 12px 36px rgba(15,23,42,.10);">'
            '<tr><td style="padding:0;background:#071526;">'
            f'<div style="height:5px;background:{accent};font-size:0;line-height:0;">&nbsp;</div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
            '<tr><td style="padding:25px 28px 27px;">'
            '<table role="presentation" cellspacing="0" cellpadding="0"><tr>'
            '<td valign="top" style="padding-right:13px;">'
            f'<div style="width:42px;height:42px;border-radius:12px;background:{accent};'
            'color:#071526;font-size:23px;line-height:42px;text-align:center;'
            'font-weight:900;">A</div></td><td valign="top">'
            f'<div style="font-size:11px;line-height:1.4;letter-spacing:1.5px;'
            f'font-weight:800;color:{accent};text-transform:uppercase;">'
            f'{html.escape(eyebrow)}</div>'
            f'<div style="margin-top:5px;font-size:25px;line-height:1.25;font-weight:800;'
            f'color:#f8fafc;">{html.escape(title)}</div>'
            f'<div style="margin-top:7px;font-size:13px;line-height:1.6;color:#94a3b8;">'
            f'{html.escape(subtitle)}</div></td></tr></table>'
            '</td></tr></table></td></tr>'
            f'<tr><td style="padding:25px 28px 28px;">{content}</td></tr>'
            '<tr><td style="padding:18px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;">'
            '<div style="font-size:12px;line-height:1.7;color:#64748b;">'
            '<b style="color:#334155;">交易纪律：</b>提醒只用于辅助观察，不代表自动交易；'
            '请结合仓位、失效价和个人风险承受能力独立决策。</div>'
            '<div style="margin-top:6px;font-size:11px;color:#94a3b8;">'
            'EvoAlpha OS · Watch Engine · 本地确定性计算</div>'
            '</td></tr></table></td></tr></table></body></html>'
        )

    @staticmethod
    def _event_actionable(event: sqlite3.Row) -> bool:
        return (
            not event["resolved_at"]
            and str(event["status"] or "UNREAD").upper()
            not in {"ACKNOWLEDGED", "RESOLVED"}
        )

    @classmethod
    def _event_should_send_immediately(cls, event: sqlite3.Row) -> bool:
        """Only completed five-minute triggers are immediate execution alerts."""
        if str(event["event_type"] or "").upper() != "TRIGGERED":
            return True
        payload = cls._json_mapping(event["payload"])
        return str(payload.get("source") or "") == "intraday_5min"

    @staticmethod
    def _skip_delivery(
        conn: sqlite3.Connection,
        delivery: sqlite3.Row,
        reason: str,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE notification_deliveries
            SET status='SKIPPED', last_error=?, sent_at=NULL, updated_at=?
            WHERE delivery_id=?
            """,
            (reason, now, delivery["delivery_id"]),
        )
        conn.commit()
        return "skipped"

    @classmethod
    def _event_identity(
        cls,
        event: sqlite3.Row | dict[str, object],
        payload: dict | None = None,
    ) -> str:
        values = payload if payload is not None else cls._json_mapping(event["payload"])
        symbol = str(event["symbol"] or "系统")
        name = str(values.get("name") or "").strip()
        if name and name != symbol:
            return f"{name} {symbol}"
        return symbol

    @classmethod
    def _event_subject(cls, event: sqlite3.Row | dict[str, object]) -> str:
        return f"{event['title']} · {cls._event_identity(event)}"

    def _enrich_watch_event(
        self,
        conn: sqlite3.Connection,
        event: sqlite3.Row,
    ) -> dict[str, object]:
        """为旧事件补齐渲染上下文，不修改事件和投递记录。"""
        enriched: dict[str, object] = dict(event)
        payload = self._json_mapping(event["payload"])
        symbol = str(event["symbol"] or "").strip()
        name = str(payload.get("name") or "").strip()
        pool_name = str(payload.get("pool_name") or "").strip()

        if symbol and (not payload.get("setup_path") or not payload.get("rule_version")):
            setup_id = str(event["setup_id"] or "")
            date_operator = "<" if payload.get("source") == "intraday_5min" else "<="
            snapshot = conn.execute(
                f"""SELECT factors, rule_version FROM watch_daily_snapshot
                   WHERE symbol=? AND trade_date{date_operator}?
                     AND (setup_id=? OR (?='' AND trade_date=?))
                   ORDER BY trade_date DESC LIMIT 1""",
                (symbol, str(event["trade_date"])[:10], setup_id, setup_id,
                 str(event["trade_date"])[:10]),
            ).fetchone()
            if snapshot is not None:
                factors = self._json_mapping(snapshot["factors"])
                if not payload.get("setup_path"):
                    payload["setup_path"] = factors.get("setup_path")
                if not payload.get("rule_version"):
                    payload["rule_version"] = snapshot["rule_version"]

        if symbol and (not name or name == symbol or not pool_name):
            snapshot = conn.execute(
                """
                SELECT name, pool_id, pool_name, current_price, zone_low, zone_high,
                       trigger_price, invalidation_price, factors
                FROM watch_daily_snapshot
                WHERE trade_date=? AND symbol=?
                LIMIT 1
                """,
                (str(event["trade_date"])[:10], symbol),
            ).fetchone()
            if snapshot is not None:
                snapshot_values = dict(snapshot)
                for key in (
                    "name",
                    "pool_id",
                    "pool_name",
                    "current_price",
                    "zone_low",
                    "zone_high",
                    "trigger_price",
                    "invalidation_price",
                ):
                    if payload.get(key) in (None, "") and snapshot_values.get(key) not in (
                        None,
                        "",
                    ):
                        payload[key] = snapshot_values[key]

        name = str(payload.get("name") or "").strip()
        if symbol and (not name or name == symbol) and self._table_exists(conn, "stock_info"):
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(stock_info)")
            }
            if {"symbol", "name"} <= columns:
                stock = conn.execute(
                    "SELECT name FROM stock_info WHERE symbol=? LIMIT 1",
                    (symbol,),
                ).fetchone()
                if stock is not None and str(stock[0] or "").strip():
                    payload["name"] = str(stock[0]).strip()

        if symbol and str(event["event_type"] or "").upper() == "INVALIDATED":
            previous_stop = conn.execute(
                """
                SELECT invalidation_price
                FROM watch_daily_snapshot
                WHERE symbol=? AND trade_date<?
                  AND watch_state IN ('IN_ZONE', 'TRIGGERED')
                  AND invalidation_price IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (symbol, str(event["trade_date"])[:10]),
            ).fetchone()
            if previous_stop is not None:
                payload["invalidation_price"] = previous_stop[0]

        enriched["payload"] = payload
        return enriched

    @staticmethod
    def _intraday_time(value: object) -> str:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return "时间未知"
        return parsed.strftime("%H:%M")

    @classmethod
    def _intraday_observations(cls, payload: dict) -> str:
        raw_rows = payload.get("observations")
        if not isinstance(raw_rows, list):
            return ""
        rows = [row for row in raw_rows if isinstance(row, dict)]
        rows = [row for row in rows if row.get("symbol") or row.get("name")]
        if not rows:
            return ""
        groups: dict[str, list[dict]] = {}
        for row in rows:
            group_name = str(row.get("pool_name") or "重点观察")
            groups.setdefault(group_name, []).append(row)

        status_meta = {
            "TRIGGERED": ("确认触发", "#047857", "#d1fae5"),
            "WAITING": ("等待触发", "#b45309", "#fef3c7"),
            "ABOVE_CHASE_LIMIT": ("超过追高线", "#be123c", "#ffe4e6"),
            "DATA_BLOCKED": ("数据异常", "#1d4ed8", "#dbeafe"),
        }
        sections = []
        for group_name, group_rows in groups.items():
            table_rows = []
            for row in group_rows:
                symbol = html.escape(str(row.get("symbol") or ""))
                name = html.escape(str(row.get("name") or symbol))
                status = str(row.get("status") or "WAITING").upper()
                label, color, background = status_meta.get(
                    status, (status or "观察", "#475569", "#e2e8f0")
                )
                distance = row.get("distance_to_trigger_pct")
                try:
                    distance_number = float(distance)
                except (TypeError, ValueError):
                    distance_text = "—"
                else:
                    if distance_number > 0:
                        distance_text = f"还差 {distance_number:.2f}%"
                    elif distance_number < 0:
                        distance_text = f"已越过 {abs(distance_number):.2f}%"
                    else:
                        distance_text = "到达触发"
                table_rows.append(
                    '<tr><td style="padding:11px 8px;border-bottom:1px solid #edf2f7;">'
                    f'<div style="font-size:13px;font-weight:800;color:#0f172a;">{name}</div>'
                    f'<div style="margin-top:2px;font-size:10px;color:#64748b;">{symbol}</div></td>'
                    '<td align="right" style="padding:11px 8px;border-bottom:1px solid #edf2f7;">'
                    f'<div style="font-size:13px;font-weight:800;">{cls._price(row.get("current_price"))}</div>'
                    f'<div style="margin-top:2px;font-size:10px;color:#64748b;">'
                    f'触发 {cls._price(row.get("trigger_price"))}</div></td>'
                    '<td align="right" style="padding:11px 8px;border-bottom:1px solid #edf2f7;">'
                    f'<div style="font-size:11px;color:#475569;">{html.escape(distance_text)}</div>'
                    f'<div style="margin-top:5px;">{cls._badge(label, color, background)}</div>'
                    '</td></tr>'
                )
            sections.append(
                '<div style="margin-top:13px;border:1px solid #dbe4ee;border-radius:12px;'
                'overflow:hidden;background:#ffffff;">'
                '<div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #dbe4ee;">'
                f'<span style="font-size:13px;font-weight:900;color:#0f172a;">'
                f'{html.escape(group_name)}</span>'
                f'<span style="margin-left:7px;font-size:11px;color:#64748b;">'
                f'{len(group_rows)} 只</span></div>'
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
                f'{"".join(table_rows)}</table></div>'
            )
        return (
            '<div style="margin-top:24px;font-size:16px;font-weight:900;color:#0f172a;">'
            f'本轮盘中观察 <span style="font-size:12px;color:#64748b;">{len(rows)} 只</span></div>'
            '<div style="margin-top:4px;font-size:11px;line-height:1.6;color:#64748b;">'
            '以同一根已完成的 5 分钟 K 线对比触发价，不使用旧日线价格触发。</div>'
            f'{"".join(sections)}'
        )

    @classmethod
    def _event_body(cls, event: sqlite3.Row | dict[str, object]) -> str:
        payload = cls._json_mapping(event["payload"])
        event_type = str(event["event_type"] or event["to_state"] or "")
        state_label, state_color, state_background = cls._state_meta(event_type)
        path_label, path_color = cls._path_meta(payload)
        symbol_raw = str(event["symbol"] or "系统")
        identity_raw = cls._event_identity(event, payload)
        identity = html.escape(identity_raw)
        is_intraday = str(payload.get("source") or "") == "intraday_5min"
        if is_intraday:
            current_price = payload.get("current_price", payload.get("close"))
            try:
                chase_limit = float(payload.get("trigger_price")) * 1.03
            except (TypeError, ValueError):
                chase_limit = None
            fields = (
                ("5分钟价", cls._price(current_price)),
                ("触发价", cls._price(payload.get("trigger_price"))),
                ("追高上限", cls._price(chase_limit)),
                ("失效价", cls._price(payload.get("invalidation_price"))),
            )
            context_label = f"5 分钟完成柱 · {cls._intraday_time(payload.get('bar_time'))}"
            context_color = "#0369a1"
        else:
            fields = (
                ("现价", cls._price(payload.get("current_price"))),
                (
                    "回调区",
                    f"{cls._price(payload.get('zone_low'))} – {cls._price(payload.get('zone_high'))}",
                ),
                ("触发价", cls._price(payload.get("trigger_price"))),
                ("失效价", cls._price(payload.get("invalidation_price"))),
            )
            context_label = path_label
            context_color = path_color
        metric_cells = "".join(
            '<td width="25%" valign="top" style="padding:12px 8px;">'
            f'<div style="font-size:11px;color:#64748b;">{html.escape(label)}</div>'
            f'<div style="margin-top:5px;font-size:16px;font-weight:800;color:#0f172a;">'
            f'{value}</div></td>'
            for label, value in fields
        )
        stock_link = cls._public_link(f"a/stock/{symbol_raw}") if event["symbol"] else ""
        from_label = cls._state_meta(str(event["from_state"] or ""))[0]
        to_label = cls._state_meta(str(event["to_state"] or event_type))[0]
        meta_parts = [str(event["trade_date"])]
        pool_name = str(payload.get("pool_name") or "").strip()
        if pool_name:
            meta_parts.append(pool_name)
        if event["from_state"] or event["to_state"]:
            meta_parts.append(f"{from_label} → {to_label}")
        meta_line = " · ".join(html.escape(part) for part in meta_parts)
        cta = (
            f'<div style="margin-top:20px;"><a href="{html.escape(stock_link)}" '
            'style="display:inline-block;padding:11px 18px;border-radius:10px;'
            'background:#071526;color:#ffffff;text-decoration:none;font-size:13px;'
            'font-weight:800;">打开个股详情 →</a></div>'
            if stock_link else ""
        )
        next_step = (
            '<div style="margin-top:14px;padding:13px 15px;border-radius:10px;'
            'background:#fff7ed;color:#9a3412;">'
            '<div style="font-size:11px;font-weight:800;">下一步</div>'
            '<div style="margin-top:5px;font-size:13px;line-height:1.7;">'
            '原低吸 setup 已失效，不再按旧触发价跟踪；如需重新观察，'
            '等待系统形成新的 setup。</div></div>'
            if event_type.upper() == "INVALIDATED"
            else ""
        )
        content = (
            '<div style="margin-bottom:16px;">'
            f'{cls._badge(state_label, state_color, state_background)}'
            f'<span style="display:inline-block;margin-left:7px;font-size:12px;'
            f'font-weight:700;color:{context_color};">{html.escape(context_label)}</span></div>'
            '<div style="padding:18px;border:1px solid #dbe4ee;border-radius:14px;'
            'background:#f8fafc;">'
            f'<div style="font-size:21px;font-weight:800;color:#0f172a;">{identity}</div>'
            f'<div style="margin-top:5px;font-size:12px;color:#64748b;">'
            f'{meta_line}'
            '</div><table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin-top:14px;background:#ffffff;border-radius:10px;">'
            f'<tr>{metric_cells}</tr></table></div>'
            '<div style="margin-top:16px;padding:15px 16px;border-left:4px solid '
            f'{state_color};background:{state_background};border-radius:0 10px 10px 0;">'
            '<div style="font-size:11px;font-weight:800;color:#64748b;letter-spacing:.5px;">'
            '为什么现在</div>'
            f'<div style="margin-top:6px;font-size:14px;line-height:1.75;color:#1e293b;">'
            f'{html.escape(str(event["message"] or "状态发生变化"))}</div></div>'
            f'{cls._strategy_note(payload)}'
            f'{next_step}{cls._intraday_observations(payload) if is_intraday else ""}{cta}'
        )
        return cls._document(
            eyebrow="EVOALPHA WATCH ALERT",
            title="EvoAlpha 低吸提醒",
            subtitle=f"{identity_raw} · {state_label} · {event['trade_date']}",
            content=content,
            accent=state_color,
        )

    def dispatch_immediate(self, trade_date: str | None = None) -> dict[str, int]:
        stats = self._stats()
        with closing(self._connect()) as conn:
            params: list[object] = list(URGENT_EVENT_TYPES)
            date_clause = ""
            if trade_date:
                date_clause = " AND trade_date=?"
                params.append(str(trade_date)[:10])
            events = conn.execute(
                f"""
                SELECT * FROM watch_events
                WHERE event_type IN ({','.join('?' for _ in URGENT_EVENT_TYPES)})
                  AND resolved_at IS NULL
                  AND COALESCE(status, 'UNREAD') NOT IN ('ACKNOWLEDGED', 'RESOLVED')
                {date_clause}
                ORDER BY created_at, event_id
                """,
                params,
            ).fetchall()
            for event in events:
                if not self._event_should_send_immediately(event):
                    continue
                delivery = self._ensure_delivery(
                    conn,
                    dedupe_key=f"email:event:{event['event_id']}",
                    event_id=event["event_id"],
                    delivery_type="event",
                )
                rendered_event = self._enrich_watch_event(conn, event)
                outcome = self._send(
                    conn,
                    delivery,
                    self._event_subject(rendered_event),
                    self._event_body(rendered_event),
                )
                if outcome:
                    stats[outcome] += 1
        return stats

    def _digest_action_card(self, row: dict) -> str:
        state = str(row.get("watch_state") or "WATCHING").upper()
        factors = self._json_mapping(row.get("factors"))
        state_label, state_color, state_background = self._state_meta(state)
        path_label, path_color = self._path_meta(factors)
        zone_text = (
            f"{self._price(row.get('zone_low'))} – {self._price(row.get('zone_high'))}"
        )
        symbol = str(row.get("symbol") or "")
        detail_link = self._public_link(f"a/stock/{symbol}")
        name_text = html.escape(str(row.get("name") or symbol))
        title = (
            f'<a href="{html.escape(detail_link)}" style="color:#0f172a;'
            f'text-decoration:none;">{name_text}</a>'
            if detail_link
            else name_text
        )
        reason = row.get("reason") or row.get("data_reason") or "等待下一步确认"
        return (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="margin-top:10px;border:1px solid #dbe4ee;border-radius:13px;'
            'background:#ffffff;"><tr><td valign="top" style="padding:15px 16px;">'
            f'<div style="font-size:16px;font-weight:800;">{title} '
            f'<span style="font-size:12px;font-weight:600;color:#64748b;">'
            f'{html.escape(symbol)}</span></div>'
            f'<div style="margin-top:8px;">{self._badge(state_label, state_color, state_background)}'
            f'<span style="margin-left:7px;font-size:12px;font-weight:700;color:{path_color};">'
            f'{html.escape(path_label)}</span></div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="margin-top:12px;background:#f8fafc;border-radius:9px;"><tr>'
            '<td width="25%" style="padding:9px 8px;"><div style="font-size:10px;color:#64748b;">现价</div>'
            f'<div style="margin-top:3px;font-size:14px;font-weight:800;">{self._price(row.get("current_price"))}</div></td>'
            '<td width="25%" style="padding:9px 8px;"><div style="font-size:10px;color:#64748b;">回调区</div>'
            f'<div style="margin-top:3px;font-size:13px;font-weight:700;">{zone_text}</div></td>'
            '<td width="25%" style="padding:9px 8px;"><div style="font-size:10px;color:#64748b;">触发价</div>'
            f'<div style="margin-top:3px;font-size:14px;font-weight:800;color:#047857;">{self._price(row.get("trigger_price"))}</div></td>'
            '<td width="25%" style="padding:9px 8px;"><div style="font-size:10px;color:#64748b;">失效价</div>'
            f'<div style="margin-top:3px;font-size:14px;font-weight:800;color:#be123c;">{self._price(row.get("invalidation_price"))}</div></td>'
            '</tr></table>'
            '<div style="margin-top:11px;font-size:12px;line-height:1.7;color:#475569;">'
            '<b style="color:#334155;">为什么现在：</b>'
            f'{html.escape(str(reason))}</div></td></tr></table>'
        )

    def _digest_body(self, conn: sqlite3.Connection, trade_date: str) -> str:
        events = conn.execute(
            "SELECT * FROM watch_events WHERE trade_date=? ORDER BY severity DESC, created_at",
            (trade_date,),
        ).fetchall()
        dataset = self._digest_dataset(conn, trade_date)
        snapshots = dataset["rows"]
        counts = self._state_counts(snapshots)
        summary_states = ("TRIGGERED", "IN_ZONE", "INVALIDATED", "UNKNOWN", "WATCHING")
        stats_cells = []
        for state in summary_states:
            label, color, background = self._state_meta(state)
            stats_cells.append(
                '<td width="20%" align="center" valign="top" '
                'style="padding:11px 4px;">'
                f'<div style="font-size:22px;font-weight:900;color:{color};">'
                f'{counts.get(state, 0)}</div>'
                f'<div style="margin-top:3px;font-size:11px;color:#64748b;">'
                f'{html.escape(label)}</div></td>'
            )
        data_ok = bool(snapshots) and all(
            str(row.get("data_status") or "").upper() == "OK" for row in snapshots
        )
        data_bad_count = sum(
            1 for row in snapshots if str(row.get("data_status") or "").upper() != "OK"
        )
        trust_badge = self._badge(
            "数据可信" if data_ok else f"数据异常 {data_bad_count}",
            "#047857" if data_ok else "#be123c",
            "#d1fae5" if data_ok else "#ffe4e6",
        )

        group_summary_rows = []
        action_sections = []
        for group in dataset["groups"]:
            group_counts = self._state_counts(group["rows"])
            group_summary_rows.append(
                '<tr><td style="padding:11px 12px;border-bottom:1px solid #e2e8f0;'
                'font-size:14px;font-weight:800;color:#0f172a;">'
                f'{html.escape(str(group["pool_name"]))}</td>'
                '<td align="right" style="padding:11px 12px;border-bottom:1px solid #e2e8f0;'
                'font-size:12px;color:#64748b;">'
                f'<b style="color:#047857;">{group_counts.get("TRIGGERED", 0)} 确认</b> · '
                f'<b style="color:#b45309;">{group_counts.get("IN_ZONE", 0)} 回调</b> · '
                f'{group_counts.get("INVALIDATED", 0)} 失效 · '
                f'{group_counts.get("WATCHING", 0)} 观察</td></tr>'
            )
            actionable = [
                row
                for row in group["rows"]
                if str(row.get("watch_state") or "").upper()
                in {"TRIGGERED", "IN_ZONE", "INVALIDATED", "UNKNOWN"}
            ]
            if not actionable:
                continue
            cards = "".join(self._digest_action_card(row) for row in actionable)
            action_sections.append(
                '<div style="margin-top:22px;padding-bottom:7px;border-bottom:2px solid #dbe4ee;">'
                f'<span style="font-size:15px;font-weight:900;color:#0f172a;">'
                f'{html.escape(str(group["pool_name"]))}</span>'
                f'<span style="margin-left:8px;font-size:11px;color:#64748b;">'
                f'{len(group["rows"])} 只 · {group_counts.get("TRIGGERED", 0)} 确认 · '
                f'{group_counts.get("IN_ZONE", 0)} 回调</span></div>{cards}'
            )
        group_summary_html = "".join(group_summary_rows) or (
            '<tr><td style="padding:12px;color:#64748b;font-size:12px;">暂无分组数据</td></tr>'
        )
        action_html = "".join(action_sections) or (
            '<div style="margin-top:10px;padding:18px;text-align:center;border-radius:12px;'
            'background:#f8fafc;color:#64748b;font-size:13px;">今日暂无需要处理的股票</div>'
        )

        event_rows = []
        for event in events:
            label, color, background = self._state_meta(str(event["event_type"] or ""))
            resolved = bool(event["resolved_at"])
            resolved_text = " · 已恢复" if resolved else ""
            event_rows.append(
                '<tr><td valign="top" style="padding:10px 0;border-bottom:1px solid #edf2f7;">'
                f'{self._badge(label, color, background)}</td>'
                '<td valign="top" style="padding:10px 0 10px 10px;border-bottom:1px solid #edf2f7;">'
                f'<div style="font-size:13px;font-weight:700;color:#1e293b;">'
                f'{html.escape(str(event["title"] or label))} · '
                f'{html.escape(str(event["symbol"] or "系统"))}{resolved_text}</div>'
                f'<div style="margin-top:3px;font-size:12px;line-height:1.6;color:#64748b;">'
                f'{html.escape(str(event["message"] or ""))}</div></td></tr>'
            )
        events_html = "".join(event_rows) or (
            '<tr><td style="padding:12px 0;color:#64748b;font-size:12px;">'
            '今日没有新的状态变化。</td></tr>'
        )
        watch_link = self._public_link("a/watchlist")
        cta = (
            f'<div style="margin-top:20px;"><a href="{html.escape(watch_link)}" '
            'style="display:inline-block;padding:12px 19px;border-radius:10px;'
            'background:#071526;color:#ffffff;text-decoration:none;font-size:13px;'
            'font-weight:800;">打开今日行动箱 →</a></div>'
            if watch_link else ""
        )
        if dataset["source"] == "manual_scan":
            source_label = f'网站历史遍历 · 规则 v{dataset["rule_version"]}'
            source_note = f'口径与 Watchlist 历史机会结果一致 · 遍历 {str(dataset["scan_id"])[:8]}'
        else:
            source_label = "正式盘后快照"
            source_note = "当日无可用历史遍历，已回退到 Watch Engine 正式快照"
        source_badge = self._badge(source_label, "#075985", "#e0f2fe")
        content = (
            '<div style="display:block;margin-bottom:13px;">'
            f'{trust_badge}<span style="margin-left:8px;font-size:12px;color:#64748b;">'
            f'覆盖 {len(snapshots)} 只持续跟踪股票 · 今日转折 {len(events)} 条</span></div>'
            f'{self._strategy_note({"rule_version": dataset["rule_version"]}, digest=True)}'
            f'<div style="margin-bottom:13px;">{source_badge}'
            f'<span style="margin-left:8px;font-size:11px;color:#64748b;">{html.escape(source_note)}</span></div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="background:#f8fafc;border-radius:13px;"><tr>{"".join(stats_cells)}</tr></table>'
            '<div style="margin-top:20px;font-size:16px;font-weight:800;color:#0f172a;">分组概览</div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin-top:8px;background:#f8fafc;border-radius:12px;overflow:hidden;">{group_summary_html}</table>'
            '<div style="margin-top:24px;font-size:16px;font-weight:800;color:#0f172a;">今日行动</div>'
            '<div style="margin-top:4px;font-size:12px;color:#64748b;">'
            '已按股票分组区分，优先处理确认触发、进入回调、失效和数据异常。</div>'
            f'{action_html}'
            '<div style="margin-top:26px;font-size:16px;font-weight:800;color:#0f172a;">正式事件记录</div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin-top:7px;">{events_html}</table>{cta}'
        )
        c_dataset = self._c_digest_dataset(conn, trade_date)
        if c_dataset["available"]:
            content = content.replace("C结构策略独立监控，本邮件不含C信号。",
                                      "C结构策略日线盘后信号见下方独立区块。")
            content += self._c_digest_body(c_dataset)
        if not c_dataset["available"] and not c_dataset["scope_count"]:
            content += self._c_digest_body(c_dataset)
        return self._document(
            eyebrow="EVOALPHA DAILY WATCH",
            title="低吸观察日报",
            subtitle=f"{trade_date} · {source_label} · 两段式低吸状态机",
            content=content,
            accent="#22d3ee" if data_ok else "#fb7185",
        )

    def _c_digest_dataset(self, conn: sqlite3.Connection, trade_date: str) -> dict:
        from quant_engine.watch.structure_monitor import StructureMonitor, SCOPE
        from quant_engine.research.structure_turning import VERSION

        scope = []
        if self._table_exists(conn, "stock_pools") and self._table_exists(conn, "stock_pool_members"):
            scope = [row[0] for row in conn.execute(
                "SELECT DISTINCT m.symbol FROM stock_pool_members m "
                "JOIN stock_pools p ON p.pool_id=m.pool_id ORDER BY m.symbol LIMIT 301")]
        base = {"available": False, "trusted": not scope, "rows": [], "events": [],
                "version": VERSION, "scope_count": len(scope), "reason": "c_run_missing"}
        smart_state = None
        if self._table_exists(conn, "smart_watchlist_state"):
            smart_state = conn.execute(
                "SELECT * FROM smart_watchlist_state WHERE pool_id='smart_mrgc_20d'"
            ).fetchone()
            if smart_state and (smart_state["status"] != "READY"
                                or smart_state["target_trade_date"] != trade_date):
                base.update(trusted=False, reason="smart_mrgc_update_untrusted")
                return base
        tables = ("structure_monitor_runs", "structure_monitor_snapshots", "structure_monitor_events")
        if not all(self._table_exists(conn, table) for table in tables):
            return base
        config_hash = StructureMonitor(self.db_path).config_hash
        params = (trade_date, VERSION, config_hash)
        run = conn.execute("SELECT * FROM structure_monitor_runs WHERE "
                           "trade_date=? AND version=? AND config_hash=?", params).fetchone()
        if run is None:
            return base
        predicate = "trade_date=? AND version=? AND config_hash=?"
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM structure_monitor_snapshots WHERE " + predicate +
            " ORDER BY symbol LIMIT 601", params)]
        events = [dict(row) for row in conn.execute(
            "SELECT * FROM structure_monitor_events WHERE " + predicate +
            " ORDER BY symbol,event_type LIMIT 1501", params)]
        raw_event_count = len(events)
        if smart_state and smart_state["initialized_date"]:
            events = [event for event in events if not (
                json.loads(event["pool_ids"]) == ["smart_mrgc_20d"]
                and event["trade_date"] <= smart_state["initialized_date"]
            )]
        covered = {row["symbol"] for row in rows if row["data_status"] == "OK"}
        run_stats = self._json_mapping(run["stats_json"])
        trusted = (run["status"] == "COMPLETED" and not run_stats.get("BLOCKED", 0) and run["scope"] == SCOPE
                   and len(scope) <= 300 and set(scope) <= covered
                   and all(row["data_status"] in {"OK", "REMOVED"} for row in rows)
                   and len(rows) <= 600 and raw_event_count <= 1500)
        base.update(available=True, trusted=trusted, rows=rows, events=events,
                    reason="ok" if trusted else "c_run_untrusted")
        return base

    def _c_digest_body(self, dataset: dict) -> str:
        if not dataset["available"]:
            return ('<div style="margin-top:26px;">C 结构策略：当前无观察成员。</div>'
                    if not dataset["scope_count"] else "")
        labels = {"BUY_CONFIRM": "买入确认", "RISK_WARNING": "风险预警",
                  "EXIT": "模拟卖出", "INVALIDATED": "结构失效", "OBSERVE": "观察结构"}
        cards = []
        priority = {key: index for index, key in enumerate(labels)}
        ordered = sorted(dataset["events"],
                         key=lambda event: (priority.get(event["event_type"], 99), event["symbol"]))
        for event in ordered[:150]:
            payload = self._json_mapping(event["payload"])
            try:
                groups = json.loads(event["pool_names"])
            except (ValueError, TypeError):
                groups = []
            groups = groups if isinstance(groups, list) else []
            identity = html.escape(str(event["name"] or event["symbol"]))
            symbol = html.escape(str(event["symbol"]))
            group = html.escape(" / ".join(str(value) for value in groups))
            label = html.escape(labels.get(event["event_type"], event["event_type"]))
            reason = html.escape(str(payload.get("reason") or "依据未记录"))
            price = self._price(payload.get("price"))
            stop = self._price(payload.get("stop_price"))
            cards.append(f'<div style="padding:12px;border-bottom:1px solid #e2e8f0;">'
                         f'<b>{label} · {identity} ({symbol})</b><br>分组：{group}'
                         f'<br>信号价格：{price} · 结构失效线：{stop}<br>依据：{reason}</div>')
        observations = sum(row["status"] == "OBSERVING"
                           for row in dataset["rows"])
        if len(ordered) > 150:
            cards.append(f'<div>其余 {len(ordered) - 150} 条事件请在 Watchlist 查看。</div>')
        if not ordered:
            cards.append('<div style="padding:12px;">当日无新增C事件。</div>')
        link = self._public_link("a/watchlist")
        if link:
            cards.append(f'<div><a href="{html.escape(link, quote=True)}">查看 C 结构策略详情</a></div>')
        note = "".join(cards)
        return ('<div style="margin-top:26px;font-size:16px;font-weight:800;">C 结构策略</div>'
                '<div style="font-size:12px;line-height:1.8;color:#64748b;">日线盘后确认 · '
                f'版本 {html.escape(dataset["version"])} · 当日观察结构 {observations} 只。'
                '模拟策略信号，不是实际持仓下单指令；模拟卖出不代表真实账户建议。</div>' + note)

    def _digest_subject(
        self,
        conn: sqlite3.Connection,
        trade_date: str,
        *,
        force: bool = False,
    ) -> str:
        counts = self._state_counts(self._digest_dataset(conn, trade_date)["rows"])
        c_dataset = self._c_digest_dataset(conn, trade_date)
        c_count = sum(event["event_type"] == "BUY_CONFIRM" for event in c_dataset["events"])
        c_suffix = f" / C {c_count} 买入确认" if c_dataset["available"] else ""
        suffix = "（人工重发）" if force else ""
        return (
            f"低吸日报 · {counts.get('TRIGGERED', 0)} 确认 / "
            f"{counts.get('IN_ZONE', 0)} 回调{c_suffix} · {trade_date}{suffix}"
        )

    @staticmethod
    def _trusted_daily_snapshot(
        conn: sqlite3.Connection,
        trade_date: str,
    ) -> tuple[bool, str]:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS snapshot_count,
                SUM(CASE WHEN UPPER(COALESCE(data_status, '')) <> 'OK' THEN 1 ELSE 0 END)
                    AS bad_data_count,
                SUM(CASE WHEN UPPER(COALESCE(watch_state, '')) = 'UNKNOWN' THEN 1 ELSE 0 END)
                    AS unknown_count
            FROM watch_daily_snapshot
            WHERE substr(trade_date, 1, 10)=?
            """,
            (trade_date,),
        ).fetchone()
        if row is None or int(row["snapshot_count"] or 0) == 0:
            return False, "snapshot_missing"
        if int(row["bad_data_count"] or 0) > 0:
            return False, "data_status_not_ok"
        if int(row["unknown_count"] or 0) > 0:
            return False, "unknown_watch_state"
        return True, "ok"

    def dispatch_digest(
        self,
        trade_date: str,
        *,
        force: bool = False,
        require_trusted: bool = False,
    ) -> dict[str, int]:
        stats = self._stats()
        date_text = str(trade_date)[:10]
        with closing(self._connect()) as conn:
            c_dataset = self._c_digest_dataset(conn, date_text)
            if not c_dataset["trusted"]:
                logger.warning("C日报可信门未通过: trade_date=%s reason=%s",
                               date_text, c_dataset["reason"])
                stats["skipped"] += 1
                return stats
            if require_trusted:
                trusted, reason = self._trusted_daily_snapshot(conn, date_text)
                if not trusted:
                    logger.warning(
                        "日报可信门未通过，暂不发送: trade_date=%s reason=%s",
                        date_text,
                        reason,
                    )
                    stats["skipped"] += 1
                    return stats
            dedupe_key = (
                f"email:digest-manual:{uuid4().hex[:12]}:{date_text}"
                if force else f"email:digest:{date_text}"
            )
            delivery = self._ensure_delivery(
                conn,
                dedupe_key=dedupe_key,
                event_id=None,
                delivery_type="digest",
            )
            outcome = self._send(
                conn,
                delivery,
                self._digest_subject(conn, date_text, force=force),
                self._digest_body(conn, date_text),
            )
            if outcome:
                stats[outcome] += 1
        return stats

    @staticmethod
    def _portfolio_exit_source(event: sqlite3.Row) -> str:
        payload = WatchNotificationDispatcher._json_mapping(
            event["payload_json"]
        )
        return str(payload.get("source") or "")

    def _portfolio_exit_event(
        self,
        conn: sqlite3.Connection,
        event_id: int,
        *,
        source: str,
    ) -> sqlite3.Row | None:
        if not self._table_exists(conn, "portfolio_exit_events"):
            return None
        row = conn.execute(
            self._portfolio_exit_select(conn)
            + " WHERE event.id=? AND event.event_status='OPEN'",
            (event_id,),
        ).fetchone()
        if row is None or self._portfolio_exit_source(row) != source:
            return None
        return row

    def _portfolio_exit_rows(
        self,
        conn: sqlite3.Connection,
        *,
        source: str,
        trade_date: str | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        if not self._table_exists(conn, "portfolio_exit_events"):
            return []
        clauses = [
            "event.event_status='OPEN'",
            """
            CASE
                WHEN json_valid(event.payload_json)
                THEN json_extract(event.payload_json, '$.source')
                ELSE NULL
            END = ?
            """,
        ]
        parameters: list[object] = [source]
        if trade_date:
            clauses.append("event.trigger_date=?")
            parameters.append(str(trade_date)[:10])
        rows = conn.execute(
            self._portfolio_exit_select(conn)
            + f"""
            WHERE {' AND '.join(clauses)}
            ORDER BY event.account_id,
                     CASE event.severity
                         WHEN 'CRITICAL' THEN 1
                         WHEN 'WARNING' THEN 2
                         ELSE 3
                     END,
                     event.id
            """
            + (f" LIMIT {int(limit)}" if limit is not None else ""),
            parameters,
        ).fetchall()
        return rows

    def _portfolio_exit_select(self, conn: sqlite3.Connection) -> str:
        """Build one bounded query that enriches events without changing their schema."""
        select_fields = ["event.*"]
        joins: list[str] = []
        if self._table_exists(conn, "stock_info"):
            select_fields.append(
                "COALESCE(NULLIF(TRIM(stock.name), ''), event.symbol) AS stock_name"
            )
            joins.append("LEFT JOIN stock_info stock ON stock.symbol=event.symbol")
        else:
            select_fields.append("event.symbol AS stock_name")
        if self._table_exists(conn, "portfolio_accounts"):
            select_fields.append("account.name AS account_name")
            joins.append(
                "LEFT JOIN portfolio_accounts account ON account.id=event.account_id"
            )
        else:
            select_fields.append("NULL AS account_name")
        if self._table_exists(conn, "portfolio_positions"):
            select_fields.extend(
                (
                    "position.quantity AS position_quantity",
                    "position.available_quantity AS current_available_quantity",
                    "position.avg_cost_micros AS avg_cost_micros",
                )
            )
            joins.append(
                "LEFT JOIN portfolio_positions position "
                "ON position.account_id=event.account_id "
                "AND position.symbol=event.symbol "
                "AND position.position_revision=event.position_revision"
            )
        else:
            select_fields.extend(
                (
                    "NULL AS position_quantity",
                    "NULL AS current_available_quantity",
                    "NULL AS avg_cost_micros",
                )
            )
        return (
            f"SELECT {', '.join(select_fields)} "
            "FROM portfolio_exit_events event "
            f"{' '.join(joins)}"
        )

    @staticmethod
    def _portfolio_exit_type_label(event: sqlite3.Row) -> str:
        event_type = str(event["event_type"] or "")
        if event_type == "TAKE_PROFIT_TRIGGERED":
            tier = int(event["tier"]) if event["tier"] is not None else 0
            return f"止盈第 {tier} 档" if tier else "止盈提醒"
        return {
            "HARD_STOP_TRIGGERED": "硬止损触发",
            "TRAILING_STOP_TRIGGERED": "移动止损触发",
            "TREND_EXIT_WATCH": "趋势转弱观察",
            "TREND_EXIT_TRIGGERED": "趋势卖点确认",
            "TIME_STOP_TRIGGERED": "时间止损触发",
            "PORTFOLIO_RISK": "组合风险提醒",
            "EXIT_DATA_BLOCKED": "卖点数据阻断",
            "EXIT_DATA_RECOVERED": "卖点数据恢复",
        }.get(event_type, event_type or "卖点提醒")

    @staticmethod
    def _portfolio_exit_price(event: sqlite3.Row) -> str:
        return WatchNotificationDispatcher._portfolio_exit_micros(
            event["observed_price_micros"]
        )

    @staticmethod
    def _portfolio_exit_micros(value: object) -> str:
        if value is None:
            return "—"
        try:
            micros = int(value)
        except (TypeError, ValueError):
            return "—"
        return f"¥{micros / 1_000_000:.4f}"

    @staticmethod
    def _portfolio_exit_value(event: sqlite3.Row, key: str) -> object:
        return event[key] if key in event.keys() else None

    @classmethod
    def _portfolio_exit_identity(cls, event: sqlite3.Row) -> tuple[str, str]:
        symbol = str(event["symbol"] or "—").strip() or "—"
        value = cls._portfolio_exit_value(event, "stock_name")
        name = str(value or symbol).strip() or symbol
        return name, symbol

    @classmethod
    def _portfolio_exit_subject_identity(cls, event: sqlite3.Row) -> str:
        name, symbol = cls._portfolio_exit_identity(event)
        safe_name = " ".join(name.split())
        safe_symbol = " ".join(symbol.split())
        return safe_symbol if safe_name == safe_symbol else f"{safe_name} {safe_symbol}"

    @classmethod
    def _portfolio_exit_reason(cls, event: sqlite3.Row) -> str:
        payload = cls._json_mapping(event["payload_json"])
        custom_reason = str(payload.get("reason") or "").strip()
        event_type = str(event["event_type"] or "")
        if (payload.get("strategy") == "C" and
                event_type in {"TREND_EXIT_WATCH", "TREND_EXIT_TRIGGERED"}):
            binding = "结构线未绑定"
            if payload.get("stop_binding") == "BOUND_AT_ENTRY":
                try:
                    stop = float(payload["stop_price"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    stop = 0.0
                if math.isfinite(stop) and stop > 0:
                    binding = f"结构线已绑定，复权结构失效线 {stop:.4f}"
            reason = custom_reason or "请核对结构失效、MA10 与 MACD 动能条件。"
            return f"策略 C：{reason}；{binding}。"
        if custom_reason:
            return custom_reason
        price = cls._portfolio_exit_price(event)
        if event_type == "TAKE_PROFIT_TRIGGERED":
            tier = int(event["tier"]) if event["tier"] is not None else 0
            threshold = cls._portfolio_exit_micros(payload.get("threshold_micros"))
            suffix = f"，计划价 {threshold}" if threshold != "—" else ""
            return f"当前价已达到第 {tier or '当前'} 档止盈条件{suffix}。"
        if event_type == "HARD_STOP_TRIGGERED":
            stop = cls._portfolio_exit_micros(payload.get("hard_stop_micros"))
            return f"现价 {price} 已触及硬止损线 {stop}，需优先处理风险。"
        if event_type == "TRAILING_STOP_TRIGGERED":
            stop = cls._portfolio_exit_micros(payload.get("trailing_stop_micros"))
            highest = cls._portfolio_exit_micros(payload.get("highest_price_micros"))
            return f"现价 {price} 已回落至移动止损线 {stop}，本轮高点 {highest}。"
        if event_type in {"TREND_EXIT_WATCH", "TREND_EXIT_TRIGGERED"}:
            try:
                close = float(payload["qfq_close"])
                ma10 = float(payload["ma10"])
                ma20 = float(payload["ma20"])
                macd_hist = float(payload["macd_hist"])
            except (KeyError, TypeError, ValueError):
                return "趋势结构已转弱，请核对 MA10、MA20 与 MACD 的共振情况。"
            return (
                f"复权收盘 {close:.2f}，MA10 {ma10:.2f}，MA20 {ma20:.2f}，"
                f"MACD 柱 {macd_hist:.4f}；趋势已转弱。"
            )
        if event_type == "TIME_STOP_TRIGGERED":
            days = payload.get("time_stop_trading_days")
            return f"持有时间已达 {days or '设定'} 个交易日，收益仍未达时间止损要求。"
        if event_type == "EXIT_DATA_BLOCKED":
            return "卖点所需行情或指标不完整，已暂停生成执行建议。"
        return "卖点条件已发生变化，请回到 Portfolio 行动箱核对。"

    @classmethod
    def _portfolio_exit_trigger_level(
        cls,
        event: sqlite3.Row,
    ) -> tuple[str, str]:
        payload = cls._json_mapping(event["payload_json"])
        event_type = str(event["event_type"] or "")
        if event_type == "HARD_STOP_TRIGGERED":
            return "硬止损线", cls._portfolio_exit_micros(
                payload.get("hard_stop_micros")
            )
        if event_type == "TRAILING_STOP_TRIGGERED":
            return "移动止损线", cls._portfolio_exit_micros(
                payload.get("trailing_stop_micros")
            )
        if event_type == "TAKE_PROFIT_TRIGGERED":
            tier = int(event["tier"]) if event["tier"] is not None else 0
            label = f"第 {tier} 档止盈线" if tier else "止盈线"
            return label, cls._portfolio_exit_micros(
                payload.get("threshold_micros")
            )
        return "卖点线", "—"

    @classmethod
    def _portfolio_exit_return(cls, event: sqlite3.Row) -> str:
        cost_value = cls._portfolio_exit_value(event, "avg_cost_micros")
        price_value = event["observed_price_micros"]
        try:
            cost = int(cost_value)
            price = int(price_value)
        except (TypeError, ValueError):
            return "—"
        if cost <= 0:
            return "—"
        result = (price / cost - 1) * 100
        return f"{result:+.2f}%"

    @classmethod
    def _portfolio_exit_card(cls, event: sqlite3.Row) -> str:
        raw_name, raw_symbol = cls._portfolio_exit_identity(event)
        name = html.escape(raw_name)
        symbol = html.escape(raw_symbol)
        event_label = html.escape(cls._portfolio_exit_type_label(event))
        severity = str(event["severity"] or "INFO")
        severity_label, severity_color, severity_background = {
            "CRITICAL": ("紧急", "#be123c", "#ffe4e6"),
            "WARNING": ("关注", "#b45309", "#fef3c7"),
            "INFO": ("提示", "#0369a1", "#e0f2fe"),
        }.get(severity, (severity, "#475569", "#e2e8f0"))
        price = html.escape(cls._portfolio_exit_price(event))
        cost = html.escape(
            cls._portfolio_exit_micros(
                cls._portfolio_exit_value(event, "avg_cost_micros")
            )
        )
        return_text = html.escape(cls._portfolio_exit_return(event))
        position_value = cls._portfolio_exit_value(event, "position_quantity")
        position_text = "—" if position_value is None else str(int(position_value))
        current_available = cls._portfolio_exit_value(
            event,
            "current_available_quantity",
        )
        available = int(
            event["available_quantity"]
            if current_available is None
            else current_available
        )
        recommended = int(event["recommended_quantity"] or 0)
        executable = bool(event["executable"])
        execution_text = "T+1 可执行" if executable else "T+1 暂不可执行"
        execution_color = "#047857" if executable else "#b45309"
        reason = html.escape(cls._portfolio_exit_reason(event))
        code_line = symbol if name == symbol else f"{symbol} · {event_label}"
        return (
            '<tr><td style="padding:8px 0 4px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="border:1px solid #e2e8f0;border-radius:13px;background:#ffffff;">'
            '<tr><td style="padding:14px 15px 10px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
            '<td valign="top">'
            f'<div style="font-size:16px;font-weight:900;color:#0f172a;">{name}</div>'
            f'<div style="margin-top:3px;font-size:11px;color:#64748b;">{code_line}</div>'
            '</td><td align="right" valign="top">'
            f'<span style="display:inline-block;padding:4px 8px;border-radius:999px;'
            f'font-size:11px;font-weight:800;color:{severity_color};'
            f'background:{severity_background};">{html.escape(severity_label)}</span>'
            f'<div style="margin-top:6px;font-size:11px;font-weight:800;'
            f'color:{execution_color};">{execution_text}</div></td></tr></table>'
            '</td></tr><tr><td style="padding:0 15px 12px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="background:#f8fafc;border-radius:10px;"><tr>'
            f'<td width="25%" style="padding:10px 8px;"><div style="font-size:10px;'
            f'color:#64748b;">现价</div><div style="margin-top:3px;font-size:13px;'
            f'font-weight:800;color:#0f172a;">{price}</div></td>'
            f'<td width="25%" style="padding:10px 8px;"><div style="font-size:10px;'
            f'color:#64748b;">成本 / 浮盈</div><div style="margin-top:3px;'
            f'font-size:12px;font-weight:800;color:#0f172a;">成本 {cost}<br>浮盈 {return_text}</div></td>'
            f'<td width="25%" style="padding:10px 8px;"><div style="font-size:10px;'
            f'color:#64748b;">持仓 / 可卖</div><div style="margin-top:3px;'
            f'font-size:12px;font-weight:800;color:#0f172a;">持仓 {position_text} 股<br>'
            f'可卖 {available} 股</div></td>'
            f'<td width="25%" style="padding:10px 8px;"><div style="font-size:10px;'
            f'color:#64748b;">建议动作</div><div style="margin-top:3px;'
            f'font-size:13px;font-weight:900;color:{execution_color};">建议 {recommended} 股</div></td>'
            '</tr></table></td></tr>'
            '<tr><td style="padding:0 15px 14px;">'
            '<div style="padding:10px 11px;border-left:3px solid #fb7185;'
            'background:#fff1f2;border-radius:0 9px 9px 0;">'
            '<div style="font-size:10px;font-weight:800;color:#9f1239;">为什么现在</div>'
            f'<div style="margin-top:4px;font-size:12px;line-height:1.65;'
            f'color:#475569;">{reason}</div></div></td></tr></table></td></tr>'
        )

    @classmethod
    def _portfolio_exit_digest_body(
        cls,
        rows: list[sqlite3.Row],
        trade_date: str,
        *,
        truncated: bool,
    ) -> str:
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows[:500]:
            grouped.setdefault(int(row["account_id"]), []).append(row)
        sections = []
        for account_id in sorted(grouped):
            account_rows = grouped[account_id]
            account_value = cls._portfolio_exit_value(account_rows[0], "account_name")
            account_name = str(account_value or f"账户 #{account_id}").strip()
            account_title = html.escape(account_name or f"账户 #{account_id}")
            sections.append(
                '<div style="margin-top:20px;font-size:15px;font-weight:900;'
                f'color:#0f172a;">{account_title}'
                f'<span style="margin-left:6px;font-size:11px;color:#94a3b8;">'
                f'#{account_id}</span>'
                f'<span style="margin-left:8px;font-size:11px;color:#64748b;">'
                f'{len(account_rows)} 条</span></div>'
                '<table role="presentation" width="100%" cellspacing="0" '
                f'cellpadding="0">{"".join(cls._portfolio_exit_card(row) for row in account_rows)}'
                '</table>'
            )
        truncation = (
            '<div style="margin-bottom:16px;padding:11px 13px;border-radius:10px;'
            'background:#fff7ed;color:#9a3412;font-size:12px;font-weight:700;">'
            '当日待处理事件超过 500 条，仅展示前 500 条；请回到 Portfolio '
            '行动箱查看完整列表。</div>'
            if truncated
            else ""
        )
        visible_rows = rows[:500]
        critical_count = sum(
            str(row["severity"] or "") == "CRITICAL" for row in visible_rows
        )
        executable_count = sum(bool(row["executable"]) for row in visible_rows)
        content = (
            f"{truncation}"
            '<div style="font-size:13px;line-height:1.7;color:#475569;">'
            f'{html.escape(trade_date)} 共汇总 {min(len(rows), 500)} 条仍为 '
            'OPEN 的日线卖点事件。事件仅作提醒，不会自动下单。</div>'
            '<table role="presentation" width="100%" cellspacing="8" cellpadding="0" '
            'style="margin-top:12px;"><tr>'
            f'<td width="33%" style="padding:10px 12px;border-radius:10px;'
            f'background:#f8fafc;"><div style="font-size:10px;color:#64748b;">'
            f'待处理</div><div style="margin-top:3px;font-size:20px;font-weight:900;">'
            f'{len(visible_rows)}</div></td>'
            f'<td width="33%" style="padding:10px 12px;border-radius:10px;'
            f'background:#fff1f2;"><div style="font-size:10px;color:#9f1239;">'
            f'紧急</div><div style="margin-top:3px;font-size:20px;font-weight:900;'
            f'color:#be123c;">{critical_count}</div></td>'
            f'<td width="33%" style="padding:10px 12px;border-radius:10px;'
            f'background:#ecfdf5;"><div style="font-size:10px;color:#047857;">'
            f'当日可执行</div><div style="margin-top:3px;font-size:20px;font-weight:900;'
            f'color:#047857;">{executable_count}</div></td></tr></table>'
            f'{"".join(sections)}'
        )
        return cls._document(
            eyebrow="EVOALPHA PORTFOLIO EXIT",
            title="Portfolio 卖点日报",
            subtitle=f"{trade_date} · 按账户与严重度排序",
            content=content,
            accent="#fb7185",
        )

    @classmethod
    def _portfolio_exit_immediate_body(cls, event: sqlite3.Row) -> str:
        name, symbol = cls._portfolio_exit_identity(event)
        identity = html.escape(
            symbol if name == symbol else f"{name} {symbol}"
        )
        event_label = html.escape(cls._portfolio_exit_type_label(event))
        trigger_label, trigger_price = cls._portfolio_exit_trigger_level(event)
        bar_time = cls._portfolio_exit_value(event, "bar_time")
        if not bar_time:
            bar_time = cls._portfolio_exit_value(event, "created_at")
        context_label = f"5 分钟完成柱 · {cls._intraday_time(bar_time)}"
        account_id = int(event["account_id"])
        account_value = cls._portfolio_exit_value(event, "account_name")
        account_name = str(account_value or f"账户 #{account_id}").strip()
        position_value = cls._portfolio_exit_value(event, "position_quantity")
        position_text = "—" if position_value is None else str(int(position_value))
        current_available = cls._portfolio_exit_value(
            event,
            "current_available_quantity",
        )
        available = int(
            event["available_quantity"]
            if current_available is None
            else current_available
        )
        recommended = int(event["recommended_quantity"] or 0)
        executable = bool(event["executable"]) and available > 0 and recommended > 0
        action_title = (
            f"建议卖出 {min(recommended, available)} 股"
            if executable
            else "T+1 暂不可执行"
        )
        action_detail = (
            f"当前可卖 {available} 股，请核对价格后手工下单。"
            if executable
            else f"当前可卖 {available} 股，保留事件并等待交易资格恢复。"
        )
        action_color = "#be123c" if executable else "#b45309"
        action_background = "#fff1f2" if executable else "#fff7ed"
        reason = html.escape(cls._portfolio_exit_reason(event))
        fields = (
            ("5分钟价", cls._portfolio_exit_price(event)),
            (trigger_label, trigger_price),
            (
                "持仓成本",
                cls._portfolio_exit_micros(
                    cls._portfolio_exit_value(event, "avg_cost_micros")
                ),
            ),
            ("当前浮盈", cls._portfolio_exit_return(event)),
        )
        metric_cells = "".join(
            '<td width="25%" valign="top" style="padding:12px 8px;">'
            f'<div style="font-size:11px;color:#64748b;">{html.escape(label)}</div>'
            f'<div style="margin-top:5px;font-size:15px;font-weight:800;'
            f'color:#0f172a;">{html.escape(value)}</div></td>'
            for label, value in fields
        )
        portfolio_link = cls._public_link("a/portfolio")
        stock_link = cls._public_link(f"a/stock/{symbol}")
        links = []
        if portfolio_link:
            links.append(
                f'<a href="{html.escape(portfolio_link)}" style="display:inline-block;'
                'padding:11px 17px;border-radius:10px;background:#071526;color:#ffffff;'
                'text-decoration:none;font-size:13px;font-weight:800;">'
                '打开 Portfolio →</a>'
            )
        if stock_link:
            links.append(
                f'<a href="{html.escape(stock_link)}" style="display:inline-block;'
                'margin-left:8px;padding:10px 16px;border:1px solid #cbd5e1;'
                'border-radius:10px;color:#334155;text-decoration:none;font-size:13px;'
                'font-weight:800;">查看个股详情</a>'
            )
        cta = f'<div style="margin-top:20px;">{"".join(links)}</div>' if links else ""
        content = (
            '<div style="margin-bottom:16px;">'
            f'{cls._badge("盘中卖点触发", "#be123c", "#ffe4e6")}'
            '<span style="display:inline-block;margin-left:7px;font-size:12px;'
            f'font-weight:700;color:#0369a1;">{html.escape(context_label)}</span></div>'
            '<div style="padding:18px;border:1px solid #dbe4ee;border-radius:14px;'
            'background:#f8fafc;">'
            f'<div style="font-size:21px;font-weight:800;color:#0f172a;">{identity}</div>'
            f'<div style="margin-top:5px;font-size:12px;color:#64748b;">'
            f'{event_label} · {html.escape(account_name)} #{account_id}</div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin-top:14px;background:#ffffff;border-radius:10px;">'
            f'<tr>{metric_cells}</tr></table>'
            '<div style="margin-top:12px;font-size:12px;line-height:1.7;color:#475569;">'
            f'持仓 {position_text} 股 · 可卖 {available} 股 · '
            f'建议 {recommended} 股</div></div>'
            f'<div style="margin-top:16px;padding:15px 16px;border-left:4px solid '
            f'{action_color};background:{action_background};border-radius:0 10px 10px 0;">'
            f'<div style="font-size:16px;font-weight:900;color:{action_color};">'
            f'{html.escape(action_title)}</div>'
            f'<div style="margin-top:5px;font-size:12px;line-height:1.65;color:#475569;">'
            f'{html.escape(action_detail)}</div></div>'
            '<div style="margin-top:16px;padding:15px 16px;border-left:4px solid '
            '#fb7185;background:#fff1f2;border-radius:0 10px 10px 0;">'
            '<div style="font-size:11px;font-weight:800;color:#9f1239;'
            'letter-spacing:.5px;">为什么现在</div>'
            f'<div style="margin-top:6px;font-size:14px;line-height:1.75;'
            f'color:#1e293b;">{reason}</div></div>'
            '<div style="margin-top:14px;font-size:11px;line-height:1.7;color:#64748b;">'
            '仅使用已完成的 5 分钟 K 线触发；系统不会自动卖出，'
            '请结合滑点、仓位和当日可卖数量独立决策。</div>'
            f'{cta}'
        )
        return cls._document(
            eyebrow="EVOALPHA PORTFOLIO EXIT",
            title="Portfolio 盘中卖点提醒",
            subtitle=(
                f"{name} {symbol} · "
                f"{cls._portfolio_exit_type_label(event)} · "
                f"{event['trigger_date']}"
            ),
            content=content,
            accent="#fb7185",
        )

    def dispatch_portfolio_exit_immediate(
        self,
        trade_date: str | None = None,
    ) -> dict[str, int]:
        stats = self._stats()
        with closing(self._connect()) as conn:
            events = self._portfolio_exit_rows(
                conn,
                source="intraday_5min",
                trade_date=trade_date,
            )
            for candidate in events:
                event_id = int(candidate["id"])
                delivery = self._ensure_delivery(
                    conn,
                    dedupe_key=f"email:portfolio-exit:event:{event_id}",
                    event_id=f"portfolio_exit:{event_id}",
                    delivery_type="portfolio_exit_event",
                )
                if delivery["status"] in {"SENT", "SKIPPED"}:
                    continue
                event = self._portfolio_exit_event(
                    conn,
                    event_id,
                    source="intraday_5min",
                )
                if event is None:
                    stats[
                        self._skip_delivery(
                            conn,
                            delivery,
                            "portfolio exit event no longer OPEN",
                        )
                    ] += 1
                    continue
                outcome = self._send(
                    conn,
                    delivery,
                    (
                        "Portfolio 卖点提醒 · "
                        f"{self._portfolio_exit_subject_identity(event)} · "
                        f"{self._portfolio_exit_type_label(event)}"
                    ),
                    self._portfolio_exit_immediate_body(event),
                )
                if outcome:
                    stats[outcome] += 1
        return stats

    def dispatch_portfolio_exit_digest(
        self,
        trade_date: str,
    ) -> dict[str, int]:
        stats = self._stats()
        date_text = str(trade_date)[:10]
        with closing(self._connect()) as conn:
            rows = self._portfolio_exit_rows(
                conn,
                source="daily",
                trade_date=date_text,
                limit=501,
            )
            if not rows:
                return stats
            delivery = self._ensure_delivery(
                conn,
                dedupe_key=f"email:portfolio-exit:digest:{date_text}",
                event_id=None,
                delivery_type="portfolio_exit_digest",
            )
            if delivery["status"] in {"SENT", "SKIPPED"}:
                return stats
            rows = self._portfolio_exit_rows(
                conn,
                source="daily",
                trade_date=date_text,
                limit=501,
            )
            if not rows:
                stats[
                    self._skip_delivery(
                        conn,
                        delivery,
                        "portfolio exit digest has no OPEN events",
                    )
                ] += 1
                return stats
            truncated = len(rows) > 500
            visible = rows[:500]
            outcome = self._send(
                conn,
                delivery,
                f"Portfolio 卖点日报 · {len(visible)} 条 · {date_text}",
                self._portfolio_exit_digest_body(
                    visible,
                    date_text,
                    truncated=truncated,
                ),
            )
            if outcome:
                stats[outcome] += 1
        return stats

    def retry_failed(self, max_attempts: int = 3) -> dict[str, int]:
        stats = self._stats()
        with closing(self._connect()) as conn:
            deliveries = conn.execute(
                """
                SELECT * FROM notification_deliveries
                WHERE status IN ('FAILED', 'PENDING') AND attempt_count<?
                  AND delivery_type != 'three_group_daily'
                ORDER BY updated_at, delivery_id
                """,
                (max_attempts,),
            ).fetchall()
            for delivery in deliveries:
                delivery_type = str(delivery["delivery_type"] or "")
                event_reference = str(delivery["event_id"] or "")
                if delivery_type == "portfolio_exit_digest":
                    trade_date = str(delivery["dedupe_key"]).rsplit(":", 1)[-1]
                    rows = self._portfolio_exit_rows(
                        conn,
                        source="daily",
                        trade_date=trade_date,
                        limit=501,
                    )
                    if not rows:
                        stats[
                            self._skip_delivery(
                                conn,
                                delivery,
                                "portfolio exit digest has no OPEN events",
                            )
                        ] += 1
                        continue
                    visible = rows[:500]
                    subject = (
                        f"Portfolio 卖点日报 · {len(visible)} 条 · {trade_date}"
                    )
                    body = self._portfolio_exit_digest_body(
                        visible,
                        trade_date,
                        truncated=len(rows) > 500,
                    )
                elif event_reference.startswith("portfolio_exit:"):
                    raw_event_id = event_reference.removeprefix(
                        "portfolio_exit:"
                    )
                    try:
                        event_id = int(raw_event_id)
                    except ValueError:
                        event_id = 0
                    event = self._portfolio_exit_event(
                        conn,
                        event_id,
                        source="intraday_5min",
                    )
                    if event is None:
                        stats[
                            self._skip_delivery(
                                conn,
                                delivery,
                                "portfolio exit event no longer OPEN",
                            )
                        ] += 1
                        continue
                    subject = (
                        "Portfolio 卖点提醒 · "
                        f"{self._portfolio_exit_subject_identity(event)} · "
                        f"{self._portfolio_exit_type_label(event)}"
                    )
                    body = self._portfolio_exit_immediate_body(event)
                elif delivery_type == "digest":
                    trade_date = str(delivery["dedupe_key"]).rsplit(":", 1)[-1]
                    if self._table_exists(conn, "smart_watchlist_state"):
                        smart = conn.execute(
                            "SELECT target_trade_date FROM smart_watchlist_state "
                            "WHERE pool_id='smart_mrgc_20d'"
                        ).fetchone()
                        if smart and smart["target_trade_date"] > trade_date:
                            stats[self._skip_delivery(
                                conn, delivery, "superseded by newer smart watchlist trading date"
                            )] += 1
                            continue
                    if not self._c_digest_dataset(conn, trade_date)["trusted"]:
                        stats["skipped"] += 1
                        continue
                    subject = self._digest_subject(conn, trade_date)
                    body = self._digest_body(conn, trade_date)
                else:
                    event = conn.execute(
                        "SELECT * FROM watch_events WHERE event_id=?",
                        (delivery["event_id"],),
                    ).fetchone()
                    if event is None:
                        stats[self._skip_delivery(
                            conn,
                            delivery,
                            "event missing before delivery",
                        )] += 1
                        continue
                    if not self._event_actionable(event):
                        stats[self._skip_delivery(
                            conn,
                            delivery,
                            "event resolved before delivery",
                        )] += 1
                        continue
                    if not self._event_should_send_immediately(event):
                        stats[self._skip_delivery(
                            conn,
                            delivery,
                            "end-of-day trigger included in digest",
                        )] += 1
                        continue
                    rendered_event = self._enrich_watch_event(conn, event)
                    subject = self._event_subject(rendered_event)
                    body = self._event_body(rendered_event)
                outcome = self._send(conn, delivery, subject, body)
                if outcome:
                    stats[outcome] += 1
        return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="EvoAlpha Watch Engine email dispatcher")
    parser.add_argument(
        "--mode",
        choices=(
            "immediate",
            "digest",
            "retry",
            "portfolio-immediate",
            "portfolio-digest",
        ),
        required=True,
    )
    parser.add_argument("--trade-date", help="YYYY-MM-DD; digest 模式必填")
    parser.add_argument(
        "--force",
        action="store_true",
        help="仅 digest：人工重发并保留独立投递记录",
    )
    parser.add_argument(
        "--require-trusted",
        action="store_true",
        help="仅 digest：正式盘后快照全部可信时才发送",
    )
    args = parser.parse_args()
    if args.require_trusted and args.mode != "digest":
        parser.error("--require-trusted 仅支持 digest 模式")
    if args.mode in {"digest", "portfolio-digest"} and not args.trade_date:
        parser.error(f"{args.mode} mode requires --trade-date")
    if args.force and args.mode.startswith("portfolio-"):
        parser.error("--force 仅支持 digest 模式")
    dispatcher = WatchNotificationDispatcher()
    if args.mode == "immediate":
        result = dispatcher.dispatch_immediate(args.trade_date)
    elif args.mode == "digest":
        result = dispatcher.dispatch_digest(
            args.trade_date,
            force=args.force,
            require_trusted=args.require_trusted,
        )
    elif args.mode == "portfolio-immediate":
        result = dispatcher.dispatch_portfolio_exit_immediate(args.trade_date)
    elif args.mode == "portfolio-digest":
        result = dispatcher.dispatch_portfolio_exit_digest(args.trade_date)
    else:
        if args.force:
            parser.error("--force 仅支持 digest 模式")
        result = dispatcher.retry_failed()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
