import type { DailyHintItem } from './dailyHints';
import type { ReportGroup } from './mrgcReportSelection';

/** Bounded local report navigation intents; never generates model commentary. */
export type AssistantIntent = 'buy' | 'exit' | 'risk' | 'sector' | 'current'
  | 'summary' | 'stocks' | 'unsupported';
export type AssistantQuestionStock = { symbol: string; name: string; sector_name?: string };
export type AssistantResolution = {
  intent: AssistantIntent;
  filter: 'all' | 'buy' | 'exit' | 'risk';
  title: string;
  group: ReportGroup;
  symbols: string[];
};

function unsupported(title: string): AssistantResolution {
  return { intent: 'unsupported', filter: 'all', group: 'all', title, symbols: [] };
}

export function resolveAssistantQuestion(
  question: string,
  stocks: readonly AssistantQuestionStock[],
  selectedSymbol: string,
): AssistantResolution {
  if (typeof question !== 'string' || !Array.isArray(stocks)) {
    return unsupported('问题或股票列表格式无效');
  }
  const text = question.trim();
  if (!text) return unsupported('请输入想查看的问题');
  if (text.length > 500) return unsupported('问题最多支持 500 字，请缩短后重试');
  if (stocks.length > 300) return unsupported('成员超过 300 只，暂不支持本地分析联动');
  if (/预测|明天.*涨|明天.*跌|目标价|保证|必涨|内幕|新闻|天气|融资|财报/.test(text)) {
    return unsupported('当前助手支持日报事实、买卖点、风险、行业与个股比较；此问题暂不支持');
  }
  const validStocks = stocks.filter((stock) => stock && typeof stock.symbol === 'string'
    && /^\d{6}$/.test(stock.symbol) && typeof stock.name === 'string');
  const codes: string[] = text.match(/(?<!\d)\d{6}(?!\d)/g) || [];
  if (codes.some((code) => !validStocks.some((stock) => stock.symbol === code))) {
    return unsupported('问题中的股票不在当前日报内，请选择分组成员');
  }
  const matches = validStocks.filter((stock) => codes.includes(stock.symbol)
    || (stock.name.trim().length >= 2 && text.includes(stock.name.trim())));
  const symbols = [...new Set(matches.map((stock) => stock.symbol))];
  if (symbols.length > 20) return unsupported('一次最多比较 20 只股票，请减少选择');
  const groups: ReportGroup[] = [];
  if (/MRGC|智能分组|近20日/i.test(text)) groups.push('mrgc');
  if (/公司|机构池/.test(text)) groups.push('company');
  if (/自选|watchlist/i.test(text)) groups.push('watchlist');
  const group: ReportGroup = /交集|重叠|同时属于/.test(text) || groups.length > 1
    ? 'overlap' : groups[0] || 'all';
  const buy = /买点|买入|买卖点|机会|值得关注/.test(text);
  const exit = /卖点|卖出|退出|止盈/.test(text);
  const risk = /风险|失效|止损|危险|预警|警惕/.test(text);
  const filter = risk ? 'risk' : exit ? 'exit' : buy ? 'buy' : 'all';
  if (symbols.length) {
    let remainder = text;
    for (const stock of matches) {
      remainder = remainder.split(stock.symbol).join('').split(stock.name.trim()).join('');
    }
    if (!buy && !exit && !risk && !/分析|比较|对比|对照|怎么样|情况|结构|持有|观察|日报/.test(text)
      && !/^[\s,，、和与及]*$/.test(remainder)) {
      return unsupported('当前助手仅支持成员股票的日报事实与比较，此问题暂不支持');
    }
    return { intent: 'stocks', filter, group, symbols,
      title: symbols.length > 1 ? `对照 ${symbols.length} 只股票的日报事实` : '查看个股日报事实' };
  }
  if (/当前|这只|该股|选中/.test(text)) {
    if (!validStocks.some((stock) => stock.symbol === selectedSymbol)) {
      return unsupported('当前股票不在这份日报中，请先选择分组成员');
    }
    return { intent: 'current', filter, group, symbols: [selectedSymbol], title: '当前股票分析' };
  }
  if (/行业|板块|集中度|分布/.test(text)) {
    return { intent: 'sector', filter, group, symbols: [], title: '行业板块分析' };
  }
  if (/买卖点/.test(text) || (buy && exit)) {
    return { intent: 'summary', filter: 'all', group, symbols: [], title: '今日买卖点概览' };
  }
  if (risk) return { intent: 'risk', filter, group, symbols: [], title: '今日风险与失效提示' };
  if (exit) return { intent: 'exit', filter, group, symbols: [], title: '今日卖出关注 · C / 缠论' };
  if (buy) return { intent: 'buy', filter, group, symbols: [], title: '今日买入关注 · C / 缠论' };
  if (group !== 'all' || /日报|总结|总览|概览|全部|整体|今天.*情况|今日.*情况/.test(text)) {
    return { intent: 'summary', filter: 'all', group, symbols: [], title: '今日日报总览' };
  }
  return unsupported('暂未识别这个问题，可查看今日机会、卖点、风险、行业、当前股票或比较成员股票');
}

/** Resolve opportunity answers against the same ordered items as the hints panel. */
export function selectAssistantHints(items: readonly DailyHintItem[], resolution: AssistantResolution) {
  const filter = resolution.filter === 'exit' ? 'sell' : resolution.filter;
  return items.slice(0, 300).filter(item =>
    (resolution.intent === 'current' || resolution.intent === 'stocks')
      ? resolution.symbols.includes(item.symbol)
      : filter === 'all' || item.direction === filter);
}
