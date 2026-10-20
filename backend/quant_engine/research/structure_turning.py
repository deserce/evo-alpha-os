"""Isolated causal structure experiment: no writes, notifications or live trades."""
from dataclasses import asdict, dataclass
from datetime import date, datetime
import math
import hashlib
import json

VERSION = 'structure_turning_experimental_v2'


@dataclass(frozen=True)
class StructureConfig:
    """ATR structure scales, independent of existing Watch strategies."""
    pivot_right: int = 2
    atr_period: int = 14
    support_tolerance_atr: float = 0.75
    stop_buffer_atr: float = 0.5
    max_entry_risk_atr: float = 4.0
    setup_window: int = 20
    pivot_pair_window: int = 45
    enable_divergence: bool = True
    enable_divergence_exit: bool = True
    divergence_lookback: int = 20
    divergence_min_gap: int = 3
    divergence_setup_window: int = 5


def _indicator_evidence(rows: list[dict], lookback: int, min_gap: int) -> list[dict]:
    """Causal RSI14 (Wilder), KDJ9/3/3 and MACD12/26/9; bounded windows."""
    evidence = []
    ema12 = ema26 = dea = gain = loss = 0.0
    k = d = 50.0
    for i, row in enumerate(rows):
        close = row['close']
        previous_close = rows[i-1]['close'] if i else close
        ema12 = close if i == 0 else ema12+(close-ema12)*2/13
        ema26 = close if i == 0 else ema26+(close-ema26)*2/27
        dif = ema12-ema26
        dea += (dif-dea)*2/10
        histogram = dif-dea
        gain = (gain*13+max(close-previous_close, 0))/14
        loss = (loss*13+max(previous_close-close, 0))/14
        rsi = 100*gain/(gain+loss) if gain+loss else 50.0
        sample = rows[max(0, i-8):i+1]
        floor = min(bar['low'] for bar in sample)
        ceiling = max(bar['high'] for bar in sample)
        rsv = 100*(close-floor)/(ceiling-floor) if ceiling > floor else 50.0
        k = k*2/3+rsv/3
        d = d*2/3+k/3
        previous = evidence[-1] if evidence else None
        oscillator = bool(previous and (rsi > previous['rsi14'] or k > previous['kdj_k']))
        spread = row['high']-row['low']
        lower_shadow = min(row['open'], close)-row['low']
        close_location = (close-row['low'])/spread if spread else 0.5
        price_stabilizing = (close > row['open'] or close > previous_close or
                             (spread > 0 and lower_shadow/spread >= 0.4 and
                              close_location >= 0.6))
        volume_sample = [bar['volume'] for bar in rows[max(0, i-5):i]]
        average_volume = sum(volume_sample)/len(volume_sample) if volume_sample else 0
        ratio = row['volume']/average_volume if average_volume > 0 else None
        volume_support = (close >= previous_close and ratio is not None and ratio >= 1.0)
        current = dict(macd_dif=dif, macd_dea=dea, macd_hist=histogram,
                       macd_dif_previous=previous['macd_dif'] if previous else None,
                       rsi14=rsi, rsi14_previous=previous['rsi14'] if previous else None,
                       kdj_k=k, kdj_d=d, kdj_j=3*k-2*d,
                       kdj_k_previous=previous['kdj_k'] if previous else None,
                       volume_ratio5=ratio, volume_support=volume_support,
                       price_stabilizing=price_stabilizing,
                       oscillator_improving=oscillator,
                       lower_shadow_ratio=lower_shadow/spread if spread else 0.0,
                       close_location=close_location,
                       bottom_hist_divergence=False, bottom_dif_divergence=False,
                       top_hist_divergence=False, top_dif_divergence=False,
                       price_breakdown=bool(i and close < rows[i-1]['low']),
                       reference_low=None, reference_high=None)
        start = max(0, i-lookback)
        end = i-min_gap+1
        if end > start:
            low_index = min(range(start, end), key=lambda index: rows[index]['low'])
            high_index = max(range(start, end), key=lambda index: rows[index]['high'])
            low_bar, high_bar = rows[low_index], rows[high_index]
            low_hist, high_hist = evidence[low_index]['macd_hist'], evidence[high_index]['macd_hist']
            current['reference_low'] = dict(trade_date=low_bar['trade_date'], price=low_bar['low'],
                                            macd_hist=low_hist, macd_dif=evidence[low_index]['macd_dif'])
            current['reference_high'] = dict(trade_date=high_bar['trade_date'], price=high_bar['high'],
                                             macd_hist=high_hist, macd_dif=evidence[high_index]['macd_dif'])
            new_low = row['low'] < min(bar['low'] for bar in rows[start:i])
            new_high = row['high'] > max(bar['high'] for bar in rows[start:i])
            current['bottom_hist_divergence'] = new_low and low_hist < histogram < 0
            current['bottom_dif_divergence'] = new_low and dif > evidence[low_index]['macd_dif']
            current['top_hist_divergence'] = new_high and 0 < histogram < high_hist
            current['top_dif_divergence'] = new_high and dif < evidence[high_index]['macd_dif']
        evidence.append(current)
    return evidence


def _bar_wording(value):
    """Translate daily prose; preserve numeric keys, timestamps and identifiers."""
    if isinstance(value, dict):
        return {key: _bar_wording(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bar_wording(item) for item in value]
    if isinstance(value, str):
        for old, new in (('连续两日', '连续两根K线'), ('前日', '前一根K线'),
                         ('当日', '当前K线'), ('当天', '当前K线'),
                         ('观察日', '观察K线'), ('信号日', '信号K线'),
                         ('日K线', 'K线'), ('日K', 'K线')):
            value = value.replace(old, new)
        if value.startswith('连续 ') and ' 日 / MA10' in value:
            value = value.replace(' 日 / MA10', ' 根K线 / MA10')
    return value


def evaluate_structure_turning(prices: list[dict], config: dict | None = None,
                               *, period: str = 'daily') -> dict:
    """Evaluate ascending completed OHLCV bars with causal confirmations.

    Daily accepts <=3000 ISO-date bars; other periods <=520 bars. Minute bars
    retain canonical local timestamps. Callers must exclude forming bars.

    Swing lows are recorded on their right-side confirmation day, never backdated.
    Entries/exits use signal close as a research approximation, not an execution
    promise. MFE/MAE exclude the entry bar and include subsequent holding bars.
    Marker factors freeze event-day evidence; state describes the last input day.
    macd_hist is DIF-DEA (not doubled). setup_atr fixes the setup's stop, whereas
    entry_risk_atr uses the event-day ATR. IDs are stable within a symbol/config;
    consumers must include symbol, version and configuration in deduplication.
    HOLDING means simulated strategy state, never an actual account position.
    """
    if period not in ('daily', 'weekly', 'monthly', '30min', '120min'):
        raise ValueError('Unsupported structure period')
    minute_period = period in ('30min', '120min')
    try:
        cfg = StructureConfig(**(config or {}))
    except TypeError as exc:
        raise ValueError('Unknown structure configuration') from exc
    for key in ('pivot_right', 'atr_period', 'setup_window', 'pivot_pair_window',
                'divergence_lookback', 'divergence_min_gap', 'divergence_setup_window'):
        value = getattr(cfg, key)
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError(f'{key}: expected integer 1..100')
    for key in ('support_tolerance_atr', 'stop_buffer_atr', 'max_entry_risk_atr'):
        value = getattr(cfg, key)
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not 0 < value <= 10):
            raise ValueError(f'{key}: expected finite number within (0,10]')
    for key in ('enable_divergence', 'enable_divergence_exit'):
        if type(getattr(cfg, key)) is not bool:
            raise ValueError(f'{key}: expected boolean')
    if cfg.divergence_min_gap >= cfg.divergence_lookback:
        raise ValueError('divergence_min_gap must be smaller than divergence_lookback')
    identity = asdict(cfg) if period == 'daily' else dict(config=asdict(cfg), period=period)
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    max_bars = 3000 if period == 'daily' else 520
    if not isinstance(prices, list) or len(prices) > max_bars:
        raise ValueError(f'prices must be a list of at most {max_bars} bars')
    rows = []
    previous_stamp = None
    for item in prices:
        try:
            day = str(item['trade_date'])
            if minute_period:
                stamp = datetime.fromisoformat(day)
                if (stamp.tzinfo is not None or len(day) not in (16, 19) or
                        day != stamp.isoformat(sep=' ', timespec=(
                            'minutes' if len(day) == 16 else 'seconds'))):
                    raise ValueError('Minute bars require canonical local ISO timestamps')
            else:
                stamp = date.fromisoformat(day)
                if stamp.isoformat() != day:
                    raise ValueError('Dates must be canonical ISO days')
            if previous_stamp is not None and stamp <= previous_stamp:
                raise ValueError('Bar times must be unique and ascending')
            if any(isinstance(item[key], bool) for key in ('open', 'high', 'low', 'close')):
                raise ValueError('Boolean prices are invalid')
            row = {key: float(item[key]) for key in ('open', 'high', 'low', 'close')}
            if any(not math.isfinite(v) or v <= 0 for v in row.values()):
                raise ValueError('Prices must be positive finite numbers')
            if not row['low'] <= min(row['open'], row['close']) <= max(
                    row['open'], row['close']) <= row['high']:
                raise ValueError('Inconsistent OHLC range')
            volume = float(item.get('volume', 0))
            if not math.isfinite(volume) or volume < 0:
                raise ValueError('Invalid volume')
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError('Malformed OHLCV bar') from exc
        rows.append(dict(row, trade_date=day, volume=volume))
        previous_stamp = stamp
    indicators = _indicator_evidence(rows, cfg.divergence_lookback, cfg.divergence_min_gap)
    markers, trades = [], []
    closes, ranges, hist = [], [], []
    ema12 = ema26 = signal = 0.0
    last_pivot = setup = position = None
    weak_days = 0
    warmup = max(25, cfg.atr_period, 2*cfg.pivot_right+1)

    def factors_for(context, stop=None):
        """Freeze today's evidence, never references to later mutable trade state."""
        evidence = dict(
            close=rows[i]['close'], atr=atr, ma5=ma5, ma10=ma10,
            previous_high=rows[i-1]['high'], macd_hist=hist[-1],
            divergence=dict(indicators[i]),
            macd_hist_previous=hist[-2], weak_days=weak_days,
            thresholds=dict(pivot_right=cfg.pivot_right, atr_period=cfg.atr_period,
                            support_tolerance_atr=cfg.support_tolerance_atr,
                            stop_buffer_atr=cfg.stop_buffer_atr,
                            max_entry_risk_atr=cfg.max_entry_risk_atr,
                            setup_window=cfg.setup_window,
                            pivot_pair_window=cfg.pivot_pair_window,
                            min_pivot_spacing_bars=3, exit_weak_days=2,
                            divergence_lookback=cfg.divergence_lookback,
                            divergence_min_gap=cfg.divergence_min_gap,
                            divergence_setup_window=cfg.divergence_setup_window),
        )
        if context is not None:
            evidence.update(
                pivot_lows=[dict(pivot) for pivot in context['pivot_lows']],
                setup_age_bars=i-context['setup_index'],
                setup_atr=context['setup_atr'],
                support_floor=(context['pivot_lows'][0]['price']-
                               cfg.support_tolerance_atr*context['setup_atr']
                               if context['pivot_lows'] else None),
                setup_path=context['setup_path'],
                divergence_at_setup=context.get('divergence_at_setup'),
                stop_price=round(stop, 6),
                entry_risk_atr=(rows[i]['close']-stop)/atr if atr > 0 else None,
            )
            if 'entry_price' in context:
                evidence.update(entry_price=context['entry_price'],
                                entry_date=context['entry_date'],
                                entry_risk_atr_at_confirmation=context['entry_risk_atr'],
                                holding_bars=i-context['entry_index'])
        checks = []

        def check(dimension, label, value, passed):
            checks.append(dict(dimension=dimension, label=label, value=value,
                               passed=bool(passed)))

        indicator = indicators[i]
        if context is not None and 'entry_price' in context:
            top_active = (cfg.enable_divergence_exit and 'top_warning_index' in context and
                          i-context['top_warning_index'] <= cfg.divergence_setup_window)
            top_exit = (top_active and indicator['price_breakdown'] and
                        rows[i]['close'] < ma5 and hist[-1] <= hist[-2])
            evidence.update(top_warning_date=context.get('top_warning_date'),
                            top_warning_active=top_active,
                            top_warning_evidence=context.get('top_warning_evidence'))
            check('exit', '收盘跌破固定失效线（满足即退出）',
                  f"收盘 {close:.4f} / 失效线 {stop:.4f}", close < stop)
            check('exit', '连续两日失守MA10且MACD走弱（满足即退出）',
                  f"连续 {weak_days} 日 / MA10 {ma10:.4f}", weak_days >= 2)
            if cfg.enable_divergence_exit:
                check('warning', '有效窗口内出现MACD正柱顶背离（仅预警）',
                      context.get('top_warning_date') or '未出现', top_active)
                check('exit', '顶背离后跌破前日低点及MA5且MACD走弱（满足即退出）',
                      f"前日低点 {rows[i-1]['low']:.4f} / MA5 {ma5:.4f}", top_exit)
        elif context is not None:
            is_divergence = context['setup_path'] == 'DIVERGENCE_REVERSAL'
            window = cfg.divergence_setup_window if is_divergence else cfg.setup_window
            age = i-context['setup_index']
            check('validity', '观察仍在有效窗口且收盘守住失效线',
                  f"第 {age} 根 / 窗口 {window} 根 / 失效线 {stop:.4f}",
                  age <= window and close >= stop)
            if is_divergence:
                origin = context['divergence_at_setup']
                reference = origin['reference_low']
                check('structure', '观察日价格创新低且MACD负柱底背离',
                      f"参考 {reference['trade_date']} 低点 {reference['price']:.4f} / "
                      f"柱 {reference['macd_hist']:.4f}", origin['bottom_hist_divergence'])
                check('momentum', 'MACD柱维持高于观察参考柱且较前日改善',
                      f"当前 {hist[-1]:.4f} / 前日 {hist[-2]:.4f}",
                      hist[-1] > reference['macd_hist'] and hist[-1] > hist[-2])
                check('price', '收阳、收盘上涨或长下影收回形成止跌',
                      f"收盘 {close:.4f} / 开盘 {rows[i]['open']:.4f} / "
                      f"前收 {rows[i-1]['close']:.4f}", indicator['price_stabilizing'])
                check('oscillator', 'RSI或KDJ的K值改善（合并为一个维度）',
                      f"RSI {indicator['rsi14']:.2f} / 前日 {indicator['rsi14_previous']:.2f}；"
                      f"K {indicator['kdj_k']:.2f} / 前日 {indicator['kdj_k_previous']:.2f}",
                      indicator['oscillator_improving'])
            else:
                check('structure', '两处摆动低点已右侧确认且观察后至少一根日K',
                      f"观察后 {age} 根", age > 0)
                check('price', '收盘收复MA5并突破前日高点',
                      f"收盘 {close:.4f} / MA5 {ma5:.4f} / 前高 {rows[i-1]['high']:.4f}",
                      close > ma5 and close > rows[i-1]['high'])
                check('momentum', 'MACD柱高于前日',
                      f"当前 {hist[-1]:.4f} / 前日 {hist[-2]:.4f}", hist[-1] > hist[-2])
            risk = (close-stop)/atr if atr > 0 else 0
            check('risk', '入场到固定失效线的风险距离在ATR上限内',
                  f"{risk:.4f} ATR / 上限 {cfg.max_entry_risk_atr:g}",
                  0 < close-stop <= cfg.max_entry_risk_atr*atr)
        evidence['checks'] = checks
        return evidence

    def mark(kind, row, reason, stop):
        context = position if position is not None else setup
        trade_id = context.get('trade_id') if context else None
        if kind == 'BUY_CONFIRM':
            trade_id = context['setup_id'] + ':entry:' + row['trade_date']
        markers.append(dict(marker_type=kind, trade_date=row['trade_date'],
                            price=round(row['close'], 6), reason=reason,
                            stop_price=round(stop, 6),
                            setup_id=context['setup_id'] if context else None,
                            trade_id=trade_id, setup_path=context['setup_path'] if context else None,
                            factors=factors_for(context, stop)))

    for i, row in enumerate(rows):
        close = row['close']
        previous_close = closes[-1] if closes else close
        ranges.append(max(row['high']-row['low'], abs(row['high']-previous_close),
                          abs(row['low']-previous_close)))
        closes.append(close)
        ema12 = close if i == 0 else ema12+(close-ema12)*2/13
        ema26 = close if i == 0 else ema26+(close-ema26)*2/27
        signal += ((ema12-ema26)-signal)*2/10
        hist.append(ema12-ema26-signal)
        if i+1 < warmup:
            continue
        atr = sum(ranges[-cfg.atr_period:])/cfg.atr_period
        ma5, ma10 = sum(closes[-5:])/5, sum(closes[-10:])/10
        improving = hist[-1] > hist[-2]
        if position is not None:
            entry = position['entry_price']
            position['mfe_pct'] = max(position['mfe_pct'], (row['high']/entry-1)*100)
            position['mae_pct'] = min(position['mae_pct'], (row['low']/entry-1)*100)
            weak_days = weak_days+1 if close < ma10 and not improving else 0
            stopped = close < position['stop_price']
            top_divergence = cfg.enable_divergence_exit and indicators[i]['top_hist_divergence']
            new_top_warning = top_divergence and ('top_warning_index' not in position or
                i-position['top_warning_index'] > cfg.divergence_setup_window)
            if top_divergence:
                position['top_warning_index'] = i
                position['top_warning_date'] = row['trade_date']
                position['top_warning_evidence'] = dict(indicators[i])
            top_breakdown = (cfg.enable_divergence_exit and
                             'top_warning_index' in position and
                             i-position['top_warning_index'] <= cfg.divergence_setup_window and
                             indicators[i]['price_breakdown'] and close < ma5 and not improving)
            if stopped or weak_days >= 2 or top_breakdown:
                reason = ('收盘跌破结构失效线' if stopped else
                          '顶背离预警后收盘跌破前日低点及MA5，MACD动能继续走弱' if top_breakdown else
                          '连续两日失守MA10且MACD动能走弱')
                mark('EXIT', row, reason, position['stop_price'])
                position.update(exit_date=row['trade_date'], exit_price=close,
                                return_pct=(close/entry-1)*100, exit_reason=reason,
                                status='CLOSED', holding_bars=i-position['entry_index'])
                position = setup = last_pivot = None
                weak_days = 0
                continue
            if weak_days == 1 or new_top_warning:
                warning_reason = ('价格创新高但MACD正柱低于参考高点，顶背离仅预警、未确认退出'
                                  if new_top_warning else '收盘失守MA10且MACD动能走弱，等待结构验证')
                mark('RISK_WARNING', row, warning_reason,
                     position['stop_price'])
        radius = cfg.pivot_right
        p = i-radius
        low = rows[p]['low']
        pivot = (all(low < rows[j]['low'] for j in range(p-radius, p)) and
                 all(low <= rows[j]['low'] for j in range(p+1, i+1)))
        if pivot:
            if (position is None and last_pivot is not None and
                    3 <= p-last_pivot[0] <= cfg.pivot_pair_window and
                    low >= last_pivot[1]-cfg.support_tolerance_atr*atr and
                    low-cfg.stop_buffer_atr*atr > 0):
                if setup is not None:
                    mark('INVALIDATED', row, '新确认回踩结构替代旧观察，重新等待确认', setup['stop'])
                pivot_lows = [
                    dict(trade_date=rows[last_pivot[0]]['trade_date'],
                         confirmed_date=last_pivot[2], price=last_pivot[1]),
                    dict(trade_date=rows[p]['trade_date'],
                         confirmed_date=row['trade_date'], price=low),
                ]
                setup_id = 'structure-v2:'+fingerprint+':pullback:' + ':'.join([
                    pivot_lows[0]['trade_date'], pivot_lows[1]['trade_date'],
                    row['trade_date'],
                ])
                setup = dict(index=i, setup_index=i, stop=low-cfg.stop_buffer_atr*atr,
                             pivot_lows=pivot_lows, setup_id=setup_id, setup_atr=atr,
                             setup_path='STRUCTURE_PULLBACK')
                mark('OBSERVE', row, '右侧确认双底或回踩低点抬高，等待价格与动能共振',
                     setup['stop'])
            last_pivot = (p, low, row['trade_date'])
        divergence = indicators[i]
        if (cfg.enable_divergence and position is None and
                divergence['bottom_hist_divergence'] and
                row['low']-cfg.stop_buffer_atr*atr > 0 and
                (setup is None or setup['setup_path'] != 'DIVERGENCE_REVERSAL')):
            if setup is not None:
                mark('INVALIDATED', row, '新的MACD柱底背离结构替代旧观察', setup['stop'])
            reference = divergence['reference_low']
            setup_id = 'structure-v2:'+fingerprint+':divergence:'+reference['trade_date']+':'+row['trade_date']
            setup = dict(index=i, setup_index=i, stop=row['low']-cfg.stop_buffer_atr*atr,
                         pivot_lows=[], setup_id=setup_id, setup_atr=atr,
                         setup_path='DIVERGENCE_REVERSAL', divergence_at_setup=dict(divergence))
            mark('OBSERVE', row, '价格创窗口新低但MACD负柱高于对应参考低点，等待当日止跌与动能共振',
                 setup['stop'])
        if setup is not None and position is None:
            setup_window = (cfg.divergence_setup_window if setup['setup_path'] == 'DIVERGENCE_REVERSAL'
                            else cfg.setup_window)
            if close < setup['stop'] or i-setup['index'] > setup_window:
                reason = '收盘跌破结构支撑' if close < setup['stop'] else '观察窗口内未形成确认'
                mark('INVALIDATED', row, reason, setup['stop'])
                setup = None
                continue
            risk = close-setup['stop']
            if setup['setup_path'] == 'DIVERGENCE_REVERSAL':
                origin = setup['divergence_at_setup']
                confirm = (hist[-1] > origin['reference_low']['macd_hist'] and improving and
                           divergence['price_stabilizing'] and divergence['oscillator_improving'])
                confirmation_reason = 'MACD柱底背离成立，价格止跌与RSI/KDJ动能改善共振；当天早期确认、非右侧底部确认'
            else:
                confirm = i > setup['index'] and close > ma5 and close > rows[i-1]['high'] and improving
                confirmation_reason = '回踩结构成立，收复MA5并突破前日高点，MACD动能改善'
            if confirm and 0 < risk <= cfg.max_entry_risk_atr*atr:
                mark('BUY_CONFIRM', row, confirmation_reason,
                     setup['stop'])
                position = dict(entry_date=row['trade_date'], entry_price=close,
                                entry_index=i, stop_price=round(setup['stop'], 6),
                                setup_id=setup['setup_id'], setup_path=setup['setup_path'],
                                divergence_at_setup=setup.get('divergence_at_setup'),
                                trade_id=setup['setup_id']+':entry:'+row['trade_date'],
                                setup_index=setup['setup_index'], setup_atr=setup['setup_atr'],
                                pivot_lows=setup['pivot_lows'], entry_risk_atr=risk/atr,
                                exit_date=None, exit_price=None, return_pct=None,
                                mfe_pct=0.0, mae_pct=0.0, status='OPEN', holding_bars=0)
                trades.append(position)
                setup = None
                weak_days = 0
    if position is not None:
        position['holding_bars'] = len(rows)-1-position['entry_index']
        position['mark_to_market_pct'] = (rows[-1]['close']/position['entry_price']-1)*100
        position['as_of_date'] = rows[-1]['trade_date']
    context = position if position is not None else setup
    stop = (position['stop_price'] if position is not None else
            setup['stop'] if setup is not None else None)
    waiting_for = []
    if len(rows) < warmup:
        waiting_for.append(f'等待至少{warmup}根有效日K线完成指标预热')
    elif position is not None:
        waiting_for.append('跟踪模拟持有：收盘跌破固定失效线，或连续两日失守MA10且MACD动能走弱时退出')
        if cfg.enable_divergence_exit:
            waiting_for.append('顶背离只预警；有效窗口内跌破前日低点及MA5、MACD走弱才确认退出')
    elif setup is not None:
        if setup['setup_path'] == 'DIVERGENCE_REVERSAL':
            origin = setup['divergence_at_setup']
            if not hist[-1] > origin['reference_low']['macd_hist']:
                waiting_for.append('MACD柱须维持高于观察日对应的参考低点柱')
            if not indicators[i]['price_stabilizing']:
                waiting_for.append('等待收阳、收盘上涨或长下影收回形成止跌')
            if not indicators[i]['oscillator_improving']:
                waiting_for.append('等待RSI或KDJ的K值较前日改善')
        else:
            if i <= setup['index']:
                waiting_for.append('观察后至少等待下一根日K线')
            if rows[-1]['close'] <= ma5:
                waiting_for.append('收盘收复MA5')
            if rows[-1]['close'] <= rows[-2]['high']:
                waiting_for.append('收盘突破前日最高价')
        if not improving:
            waiting_for.append('MACD柱高于前日，动能改善')
        if not 0 < rows[-1]['close']-stop <= cfg.max_entry_risk_atr*atr:
            waiting_for.append(f'确认价与固定失效线距离须在0至{cfg.max_entry_risk_atr:g}倍当日ATR内')
    else:
        waiting_for.append('等待两处经右侧确认的摆动低点形成双底或低点抬高结构')
        if cfg.enable_divergence:
            waiting_for.append('或等待价格创新低但MACD负柱未创新低，再由止跌与RSI/KDJ改善确认')
    state = dict(status='HOLDING' if position is not None else
                        'OBSERVING' if setup is not None else 'WATCHING',
                 trade_date=rows[-1]['trade_date'] if rows else None,
                 setup_id=context['setup_id'] if context else None,
                 setup_path=context['setup_path'] if context else None,
                 trade_id=position['trade_id'] if position else None,
                 stop_price=round(stop, 6) if stop is not None else None,
                 entry_date=position['entry_date'] if position else None,
                 entry_price=position['entry_price'] if position else None,
                 waiting_for=waiting_for,
                 factors=factors_for(context, stop) if len(rows) >= warmup else {})
    for trade in trades:
        trade.pop('entry_index', None)
        trade.pop('setup_index', None)
        trade.pop('top_warning_index', None)
        for key in ('mfe_pct', 'mae_pct', 'return_pct', 'mark_to_market_pct'):
            if trade.get(key) is not None:
                trade[key] = round(trade[key], 4)
    closed = sum(trade['status'] == 'CLOSED' for trade in trades)
    result = dict(version=VERSION, config=asdict(cfg), config_fingerprint=fingerprint,
                markers=markers, trades=trades, state=state,
                summary=dict(status='ok' if len(rows) >= warmup else 'insufficient_history',
                             bars=len(rows), closed_trades=closed, open_trades=len(trades)-closed,
                             simulation='信号日收盘模拟；未计滑点、费用、涨跌停及T+1；非真实成交',
                             methodology='因果右侧摆动确认；ATR结构尺度；完整生命周期MFE/MAE'))

    if period != 'daily':
        result = _bar_wording(result)
        result.update(period=period, window_unit='bars', chart_only=True)
    return result
