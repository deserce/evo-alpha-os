export type WatchFactorMap = Record<string, unknown>;

type RepairRuleSubset = {
  repair_path_enabled?: boolean;
  repair_market_states?: string[];
  repair_industry_states?: string[];
  pullback_max_pct?: number;
  repair_pullback_max_pct?: number;
  repair_setup_score_min?: number;
  repair_trigger_score_min?: number;
  repair_confirmation_min_count?: number;
  repair_volume_ratio_max_pct?: number;
  repair_reversal_volume_ratio_max_pct?: number;
  constructive_max_decline_pct?: number;
};

function finiteNumber(value: unknown) {
  const parsed = Number(value);
  return value === null || value === undefined || value === '' || !Number.isFinite(parsed)
    ? null
    : parsed;
}

export function setupPathLabel(value: unknown) {
  if (value === 'TREND_PULLBACK') return '路径 A · 强势回调';
  if (value === 'REPAIR_REVERSAL') return '路径 B · 领先修复';
  return '未入区';
}

export function setupPathCode(value: unknown) {
  if (value === 'TREND_PULLBACK') return 'A';
  if (value === 'REPAIR_REVERSAL') return 'B';
  return '-';
}

export function confidenceLabel(value: unknown) {
  if (value === 'HIGH') return '高置信';
  if (value === 'MEDIUM') return '中置信';
  if (value === 'LOW') return '低置信';
  return '未评估';
}

function signed(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)}`;
}

export function describeWatchFactors(factors?: WatchFactorMap | null) {
  if (!factors) return [];
  const labels = [setupPathLabel(factors.setup_path)];
  const repairScore = finiteNumber(factors.repair_setup_score);
  if (factors.setup_path === 'REPAIR_REVERSAL' && repairScore !== null) {
    labels.push(
      `修复分 ${repairScore.toFixed(repairScore % 1 === 0 ? 0 : 1)} · ${confidenceLabel(factors.repair_confidence)}`,
    );
  }
  const marketDelta = finiteNumber(factors.market_strength_delta);
  if (marketDelta !== null) labels.push(`市场 ${signed(marketDelta)}`);
  const industryDelta = finiteNumber(factors.industry_strength_delta);
  if (industryDelta !== null) labels.push(`行业 ${signed(industryDelta)}`);
  const confirmationCount = finiteNumber(factors.repair_confirmation_count);
  if (confirmationCount !== null) {
    labels.push(`确认共振 ${Math.round(confirmationCount)}/4`);
  }
  return labels;
}

export function validateRepairRuleConfig(config: RepairRuleSubset) {
  if (!config.repair_path_enabled) return [];
  const errors: string[] = [];
  if (!config.repair_market_states?.length || !config.repair_industry_states?.length) {
    errors.push('修复路径至少选择一个市场和行业状态');
  }
  if (
    finiteNumber(config.repair_pullback_max_pct) !== null
    && finiteNumber(config.pullback_max_pct) !== null
    && Number(config.repair_pullback_max_pct) < Number(config.pullback_max_pct)
  ) {
    errors.push('修复路径最大回撤不能小于强势回调上限');
  }
  if (
    finiteNumber(config.repair_trigger_score_min) !== null
    && finiteNumber(config.repair_setup_score_min) !== null
    && Number(config.repair_trigger_score_min) < Number(config.repair_setup_score_min)
  ) {
    errors.push('修复路径触发分不能低于入区分');
  }
  const confirmationCount = finiteNumber(config.repair_confirmation_min_count);
  if (confirmationCount === null || confirmationCount < 2 || confirmationCount > 4) {
    errors.push('修复确认共振数必须在 2-4 之间');
  }
  const regularVolumeCap = finiteNumber(config.repair_volume_ratio_max_pct);
  const reversalVolumeCap = finiteNumber(config.repair_reversal_volume_ratio_max_pct);
  if (
    regularVolumeCap !== null
    && reversalVolumeCap !== null
    && reversalVolumeCap < regularVolumeCap
  ) {
    errors.push('反转阳线量能上限不能低于普通修复量能上限');
  }
  const constructiveDecline = finiteNumber(config.constructive_max_decline_pct);
  if (
    constructiveDecline !== null
    && (constructiveDecline < 0.1 || constructiveDecline > 5)
  ) {
    errors.push('建设性整理最大跌幅必须在 0.1%-5% 之间');
  }
  return errors;
}
