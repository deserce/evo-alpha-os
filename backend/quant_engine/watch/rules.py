"""可版本化的低吸规则配置。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping


ALLOWED_MARKET_STATES = {"M0", "M1", "M2", "M3", "M4"}
ALLOWED_INDUSTRY_STATES = {"I0", "I1", "I2", "I3", "I4"}
ALLOWED_CONFIRMATION_METHODS = {
    "break_prev_high",
    "reclaim_ma10",
    "reversal_confluence",
    "structure_macd",
    "constructive_consolidation",
}
SHARED_CONFIRMATION_METHODS = ALLOWED_CONFIRMATION_METHODS - {
    "constructive_consolidation"
}


@dataclass(frozen=True)
class LowBuyRules:
    """Watch Engine 使用的单一规则版本。百分比均按 0-100 表示。"""

    version: int = 0
    market_states: tuple[str, ...] = ("M2", "M3", "M4")
    industry_states: tuple[str, ...] = ("I3", "I4")
    rps50_min: float = 85.0
    structure_score_min: float = 70.0
    pullback_min_pct: float = 5.0
    pullback_max_pct: float = 15.0
    near_ma_pct: float = 3.0
    trend_include_ma10_support: bool = False
    volume_ratio_max_pct: float = 80.0
    volume_shrink_days: int = 3
    max_stop_loss_pct: float = 8.0
    chase_limit_pct: float = 3.0
    setup_valid_days: int = 5
    cooldown_trade_days: int = 7
    structure_lookback_days: int = 15
    double_bottom_tolerance_pct: float = 3.0
    macd_improve_days: int = 1
    confirmation_methods: tuple[str, ...] = (
        "reversal_confluence",
        "structure_macd",
        "constructive_consolidation",
        "break_prev_high",
        "reclaim_ma10",
    )
    require_above_ma50: bool = True
    repair_path_enabled: bool = False
    repair_market_states: tuple[str, ...] = ("M1", "M2", "M3", "M4")
    repair_industry_states: tuple[str, ...] = ("I0", "I1", "I2", "I3", "I4")
    repair_setup_score_min: float = 50.0
    repair_trigger_score_min: float = 60.0
    repair_pullback_max_pct: float = 30.0
    repair_ma50_below_max_pct: float = 15.0
    repair_volume_ratio_max_pct: float = 95.0
    repair_reversal_volume_ratio_max_pct: float = 120.0
    constructive_max_decline_pct: float = 2.0
    repair_confirmation_min_count: int = 2
    repair_min_rps_anchor: float = 60.0
    market_rebound_delta_min: float = 8.0
    industry_rebound_delta_min: float = 5.0

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Any] | None = None, *, version: int = 0
    ) -> "LowBuyRules":
        raw = dict(values or {})
        allowed = set(cls().to_dict())
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"未知参数: {', '.join(unknown)}")

        defaults = cls().to_dict()
        # 新字段不能反向改变历史规则版本的复现结果。
        # 历史 JSON 没有该字段时继续沿用只参考 MA20/MA50 的旧口径。
        if version > 0 and values is not None and "trend_include_ma10_support" not in raw:
            defaults["trend_include_ma10_support"] = False
        defaults.update(raw)
        market_states = _state_list(
            defaults["market_states"], ALLOWED_MARKET_STATES, "市场状态"
        )
        industry_states = _state_list(
            defaults["industry_states"], ALLOWED_INDUSTRY_STATES, "行业状态"
        )
        repair_market_states = _state_list(
            defaults["repair_market_states"], ALLOWED_MARKET_STATES, "修复路径市场状态"
        )
        repair_industry_states = _state_list(
            defaults["repair_industry_states"],
            ALLOWED_INDUSTRY_STATES,
            "修复路径行业状态",
        )
        methods = _state_list(
            defaults["confirmation_methods"],
            ALLOWED_CONFIRMATION_METHODS,
            "确认方式",
            uppercase=False,
        )
        if not SHARED_CONFIRMATION_METHODS.intersection(methods):
            raise ValueError("路径 A 至少选择一种共享确认方式")
        rps50_min = _number(defaults["rps50_min"], "RPS50 下限", 0, 100)
        structure_score_min = _number(
            defaults["structure_score_min"], "结构分下限", 0, 100
        )
        pullback_min = _number(defaults["pullback_min_pct"], "回撤下限", 0, 100)
        pullback_max = _number(defaults["pullback_max_pct"], "回撤上限", 0, 100)
        if pullback_min > pullback_max:
            raise ValueError("回撤下限不能大于回撤上限")
        near_ma = _number(defaults["near_ma_pct"], "均线距离", 0, 20)
        trend_include_ma10_support = defaults["trend_include_ma10_support"]
        if not isinstance(trend_include_ma10_support, bool):
            raise ValueError("MA10 趋势支撑开关必须为布尔值")
        volume_ratio = _number(
            defaults["volume_ratio_max_pct"], "量能比例上限", 0, 200
        )
        shrink_days = _integer(defaults["volume_shrink_days"], "缩量天数", 2, 10)
        stop_loss = _number(defaults["max_stop_loss_pct"], "最大止损", 0.1, 30)
        chase_limit = _number(defaults["chase_limit_pct"], "追高上限", 0, 20)
        setup_valid_days = _integer(
            defaults["setup_valid_days"], "确认窗口", 1, 20
        )
        cooldown = _integer(defaults["cooldown_trade_days"], "冷却交易日", 0, 60)
        structure_lookback_days = _integer(
            defaults["structure_lookback_days"], "价格结构回看", 7, 30
        )
        double_bottom_tolerance_pct = _number(
            defaults["double_bottom_tolerance_pct"], "双底容差", 0.1, 10
        )
        macd_improve_days = _integer(
            defaults["macd_improve_days"], "MACD 改善天数", 1, 3
        )
        repair_setup_score_min = _number(
            defaults["repair_setup_score_min"], "修复路径入区分", 0, 100
        )
        repair_trigger_score_min = _number(
            defaults["repair_trigger_score_min"], "修复路径触发分", 0, 100
        )
        if repair_trigger_score_min < repair_setup_score_min:
            raise ValueError("修复路径触发分不能低于入区分")
        repair_pullback_max_pct = _number(
            defaults["repair_pullback_max_pct"], "修复路径最大回撤", 5, 60
        )
        repair_ma50_below_max_pct = _number(
            defaults["repair_ma50_below_max_pct"], "MA50 下方容忍", 0, 30
        )
        repair_volume_ratio_max_pct = _number(
            defaults["repair_volume_ratio_max_pct"], "修复路径量能比例", 20, 200
        )
        repair_reversal_volume_ratio_max_pct = _number(
            defaults["repair_reversal_volume_ratio_max_pct"],
            "修复反转放量上限",
            20,
            200,
        )
        if repair_reversal_volume_ratio_max_pct < repair_volume_ratio_max_pct:
            raise ValueError("修复反转放量上限不能低于修复路径量能比例")
        constructive_max_decline_pct = _number(
            defaults["constructive_max_decline_pct"],
            "建设性整理最大跌幅",
            0.1,
            5,
        )
        repair_confirmation_min_count = _integer(
            defaults["repair_confirmation_min_count"], "修复路径确认数量", 2, 4
        )
        repair_min_rps_anchor = _number(
            defaults["repair_min_rps_anchor"], "修复路径 RPS 锚点", 0, 100
        )
        market_rebound_delta_min = _number(
            defaults["market_rebound_delta_min"], "市场改善分", 0, 100
        )
        industry_rebound_delta_min = _number(
            defaults["industry_rebound_delta_min"], "行业改善分", 0, 100
        )
        require_above = defaults["require_above_ma50"]
        if not isinstance(require_above, bool):
            raise ValueError("MA50 约束必须为布尔值")
        repair_path_enabled = defaults["repair_path_enabled"]
        if not isinstance(repair_path_enabled, bool):
            raise ValueError("修复路径开关必须为布尔值")
        return cls(
            version=version,
            market_states=market_states,
            industry_states=industry_states,
            rps50_min=rps50_min,
            structure_score_min=structure_score_min,
            pullback_min_pct=pullback_min,
            pullback_max_pct=pullback_max,
            near_ma_pct=near_ma,
            trend_include_ma10_support=trend_include_ma10_support,
            volume_ratio_max_pct=volume_ratio,
            volume_shrink_days=shrink_days,
            max_stop_loss_pct=stop_loss,
            chase_limit_pct=chase_limit,
            setup_valid_days=setup_valid_days,
            cooldown_trade_days=cooldown,
            structure_lookback_days=structure_lookback_days,
            double_bottom_tolerance_pct=double_bottom_tolerance_pct,
            macd_improve_days=macd_improve_days,
            confirmation_methods=methods,
            require_above_ma50=require_above,
            repair_path_enabled=repair_path_enabled,
            repair_market_states=repair_market_states,
            repair_industry_states=repair_industry_states,
            repair_setup_score_min=repair_setup_score_min,
            repair_trigger_score_min=repair_trigger_score_min,
            repair_pullback_max_pct=repair_pullback_max_pct,
            repair_ma50_below_max_pct=repair_ma50_below_max_pct,
            repair_volume_ratio_max_pct=repair_volume_ratio_max_pct,
            repair_reversal_volume_ratio_max_pct=repair_reversal_volume_ratio_max_pct,
            constructive_max_decline_pct=constructive_max_decline_pct,
            repair_confirmation_min_count=repair_confirmation_min_count,
            repair_min_rps_anchor=repair_min_rps_anchor,
            market_rebound_delta_min=market_rebound_delta_min,
            industry_rebound_delta_min=industry_rebound_delta_min,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_states": list(self.market_states),
            "industry_states": list(self.industry_states),
            "rps50_min": self.rps50_min,
            "structure_score_min": self.structure_score_min,
            "pullback_min_pct": self.pullback_min_pct,
            "pullback_max_pct": self.pullback_max_pct,
            "near_ma_pct": self.near_ma_pct,
            "trend_include_ma10_support": self.trend_include_ma10_support,
            "volume_ratio_max_pct": self.volume_ratio_max_pct,
            "volume_shrink_days": self.volume_shrink_days,
            "max_stop_loss_pct": self.max_stop_loss_pct,
            "chase_limit_pct": self.chase_limit_pct,
            "setup_valid_days": self.setup_valid_days,
            "cooldown_trade_days": self.cooldown_trade_days,
            "structure_lookback_days": self.structure_lookback_days,
            "double_bottom_tolerance_pct": self.double_bottom_tolerance_pct,
            "macd_improve_days": self.macd_improve_days,
            "confirmation_methods": list(self.confirmation_methods),
            "require_above_ma50": self.require_above_ma50,
            "repair_path_enabled": self.repair_path_enabled,
            "repair_market_states": list(self.repair_market_states),
            "repair_industry_states": list(self.repair_industry_states),
            "repair_setup_score_min": self.repair_setup_score_min,
            "repair_trigger_score_min": self.repair_trigger_score_min,
            "repair_pullback_max_pct": self.repair_pullback_max_pct,
            "repair_ma50_below_max_pct": self.repair_ma50_below_max_pct,
            "repair_volume_ratio_max_pct": self.repair_volume_ratio_max_pct,
            "repair_reversal_volume_ratio_max_pct": self.repair_reversal_volume_ratio_max_pct,
            "constructive_max_decline_pct": self.constructive_max_decline_pct,
            "repair_confirmation_min_count": self.repair_confirmation_min_count,
            "repair_min_rps_anchor": self.repair_min_rps_anchor,
            "market_rebound_delta_min": self.market_rebound_delta_min,
            "industry_rebound_delta_min": self.industry_rebound_delta_min,
        }


def _state_list(
    value: Any, allowed: set[str], label: str, *, uppercase: bool = True
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{label}至少选择一项")
    normalized = tuple(
        dict.fromkeys(
            str(item).strip().upper() if uppercase else str(item).strip().lower()
            for item in value
        )
    )
    invalid = sorted(set(normalized) - allowed)
    if invalid:
        raise ValueError(f"{label}无效: {', '.join(invalid)}")
    return normalized


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数字")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{label}必须在 {minimum:g}-{maximum:g} 之间")
    return result


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是整数") from exc
    if result != value or not minimum <= result <= maximum:
        raise ValueError(f"{label}必须是 {minimum}-{maximum} 的整数")
    return result


def ensure_rule_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watch_rule_configs (
            version INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            activated_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_watch_rule_status "
        "ON watch_rule_configs (status, version DESC)"
    )


def load_active_rules(conn: sqlite3.Connection) -> LowBuyRules:
    """读取当前启用版本；首次运行自动落下版本 1 默认规则。"""
    ensure_rule_schema(conn)
    row = conn.execute(
        "SELECT version, config_json FROM watch_rule_configs "
        "WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rules = LowBuyRules.from_mapping(version=1)
        conn.execute(
            "INSERT INTO watch_rule_configs "
            "(version, status, config_json, created_at, activated_at) "
            "VALUES (1, 'ACTIVE', ?, ?, ?)",
            (json.dumps(rules.to_dict(), ensure_ascii=False), now, now),
        )
        return rules
    try:
        values = json.loads(str(row[1]))
    except json.JSONDecodeError as exc:
        raise ValueError(f"规则版本 {row[0]} 配置损坏") from exc
    return LowBuyRules.from_mapping(values, version=int(row[0]))


def save_rule_config(
    conn: sqlite3.Connection,
    values: Mapping[str, Any],
    *,
    activate: bool,
) -> LowBuyRules:
    """保存新的草稿或启用版本，不原地修改历史版本。"""
    ensure_rule_schema(conn)
    validated = LowBuyRules.from_mapping(values)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM watch_rule_configs").fetchone()
    version = int(row[0])
    rules = replace(validated, version=version)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "ACTIVE" if activate else "DRAFT"
    if activate:
        conn.execute(
            "UPDATE watch_rule_configs SET status='ARCHIVED' "
            "WHERE status IN ('ACTIVE', 'DRAFT')"
        )
    conn.execute(
        "INSERT INTO watch_rule_configs "
        "(version, status, config_json, created_at, activated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            version,
            status,
            json.dumps(rules.to_dict(), ensure_ascii=False),
            now,
            now if activate else None,
        ),
    )
    return rules


def rule_config_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_rule_schema(conn)
    rows = conn.execute(
        "SELECT version, status, config_json, created_at, activated_at "
        "FROM watch_rule_configs ORDER BY version DESC"
    ).fetchall()
    result = []
    for row in rows:
        version = int(row[0])
        stored_config = json.loads(str(row[2]))
        result.append(
            {
                "version": version,
                "status": str(row[1]),
                "config": LowBuyRules.from_mapping(
                    stored_config, version=version
                ).to_dict(),
                "created_at": row[3],
                "activated_at": row[4],
            }
        )
    return result
