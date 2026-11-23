"""Reusable indicators without provider, strategy or delivery dependencies."""

from dataclasses import dataclass

from .models import DailyBar


@dataclass(frozen=True)
class SarResult:
    value: float | None
    direction: str
    reversal: str
    recent_reversal: str = "无"


@dataclass(frozen=True)
class AlignmentResult:
    direction: str
    candle_position: str


@dataclass(frozen=True)
class TrendResult:
    vwap: float | None
    adx: float | None
    plus_di: float | None
    minus_di: float | None
    adx_rising: bool | None
    obv_rising_three_days: bool | None
    rsi: float | None
    rsi_history: tuple[float | None, ...] = ()


@dataclass(frozen=True)
class PriceDivergence:
    label: str
    confirmed_at: int


def nine_turn_streak(closes: list[float]) -> int:
    """Return the signed streak ending at the latest close, in chronological order."""
    streak = 0
    for close, earlier in zip(closes[4:], closes):
        if close > earlier:
            streak = max(streak, 0) + 1
        elif close < earlier:
            streak = min(streak, 0) - 1
        else:
            streak = 0
    return streak


def price_divergence_signals(
    bars: list[DailyBar], indicator: tuple[float | None, ...],
) -> tuple[PriceDivergence, ...]:
    """Compare oscillator values at confirmed price pivots on the same dates."""
    if len(bars) != len(indicator):
        raise ValueError("price and indicator history must have the same length")
    # Input is chronological. Each pivot needs two bars on either side.
    pivot_radius = 2
    size = len(bars)
    highs, lows = [], []
    for index in range(max(pivot_radius, size - 60), size - pivot_radius):
        high, low = bars[index].high, bars[index].low
        neighbors = bars[index - pivot_radius:index] + bars[index + 1:index + pivot_radius + 1]
        if all(high > bar.high for bar in neighbors):
            highs.append(index)
        if all(low < bar.low for bar in neighbors):
            lows.append(index)

    signals = []
    for pivots, is_top in ((highs, True), (lows, False)):
        if len(pivots) < 2 or pivots[-1] < size - 10:
            continue
        previous, latest = pivots[-2:]
        old_value, new_value = indicator[previous], indicator[latest]
        if old_value is None or new_value is None:
            continue
        if is_top and bars[latest].high > bars[previous].high and new_value < old_value:
            signals.append(PriceDivergence("顶背离", latest + pivot_radius))
        if not is_top and bars[latest].low < bars[previous].low and new_value > old_value:
            signals.append(PriceDivergence("底背离", latest + pivot_radius))
    return tuple(signals)


def price_divergences(
    bars: list[DailyBar], indicator: tuple[float | None, ...],
) -> tuple[str, ...]:
    return tuple(signal.label for signal in price_divergence_signals(bars, indicator))


def sar_indicator(bars: list[DailyBar], step: float = 0.02, maximum: float = 0.2) -> SarResult:
    """Parabolic SAR; red means rising, green means falling, flip is today only."""
    if not 0 < step <= maximum:
        raise ValueError("SAR requires 0 < step <= maximum")
    ordered = sorted(bars, key=lambda bar: bar.date)
    if len(ordered) < 2:
        return SarResult(None, "数据不足", "无", "无")
    rising = ordered[1].close >= ordered[0].close
    value = ordered[0].low if rising else ordered[0].high
    extreme = ordered[0].high if rising else ordered[0].low
    acceleration = step
    reversal = "无"
    last_reversal_index = None
    last_reversal = "无"
    for index in range(1, len(ordered)):
        bar = ordered[index]
        previous = ordered[max(0, index - 2):index]
        candidate = value + acceleration * (extreme - value)
        reversal = "无"
        if rising:
            candidate = min(candidate, *(item.low for item in previous))
            if bar.low < candidate:
                rising, value, extreme, acceleration = False, max(extreme, bar.high), bar.low, step
                reversal = "翻绿"
                last_reversal_index, last_reversal = index, reversal
            else:
                value = candidate
                if bar.high > extreme:
                    extreme, acceleration = bar.high, min(maximum, acceleration + step)
        else:
            candidate = max(candidate, *(item.high for item in previous))
            if bar.high > candidate:
                rising, value, extreme, acceleration = True, min(extreme, bar.low), bar.high, step
                reversal = "翻红"
                last_reversal_index, last_reversal = index, reversal
            else:
                value = candidate
                if bar.low < extreme:
                    extreme, acceleration = bar.low, min(maximum, acceleration + step)
    recent_reversal = "无"
    if last_reversal_index is not None and len(ordered) - 1 - last_reversal_index <= 2:
        days = ("今日", "昨天", "前天")
        recent_reversal = f"{days[len(ordered) - 1 - last_reversal_index]}{last_reversal}"
    return SarResult(value, "红色" if rising else "绿色", reversal, recent_reversal)


def moving_average_alignment(bars: list[DailyBar], adhesion: float = 0.005) -> AlignmentResult:
    """Require MA60 slope, strict ordering, and separated adjacent averages."""
    ordered = sorted(bars, key=lambda bar: bar.date)
    if len(ordered) < 61:
        return AlignmentResult("数据不足", "数据不足")
    closes = [bar.close for bar in ordered]
    averages = [sum(closes[-period:]) / period for period in (5, 10, 20, 60)]
    previous60 = sum(closes[-61:-1]) / 60
    latest = ordered[-1]
    position = "上方" if latest.low > max(averages) else "下方" if latest.high < min(averages) else "穿越均线组"
    pairs = list(zip(averages, averages[1:]))
    if any(abs(left - right) / abs(right) <= adhesion for left, right in pairs):
        return AlignmentResult("均线粘合", position)
    if all(left > right for left, right in pairs) and averages[-1] > previous60:
        return AlignmentResult("多头排列", position)
    if all(left < right for left, right in pairs) and averages[-1] < previous60:
        return AlignmentResult("空头排列", position)
    return AlignmentResult("均线缠绕或趋势未确认", position)


def vwap_indicator(bars: list[DailyBar]) -> float | None:
    total_volume = sum(bar.volume for bar in bars)
    return sum((bar.high + bar.low + bar.close) / 3 * bar.volume for bar in bars) / total_volume if total_volume else None


def _rsi_from_averages(gain: float, loss: float) -> float:
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


def rsi_series(closes: list[float], period: int = 14) -> tuple[float | None, ...]:
    if len(closes) <= period:
        return (None,) * len(closes)
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    values: list[float | None] = [None] * period
    values.append(_rsi_from_averages(average_gain, average_loss))
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        values.append(_rsi_from_averages(average_gain, average_loss))
    return tuple(values)


def rsi_indicator(closes: list[float], period: int = 14) -> float | None:
    values = rsi_series(closes, period)
    return values[-1] if values else None


def trend_indicators(bars: list[DailyBar]) -> TrendResult:
    ordered = sorted(bars, key=lambda bar: bar.date)
    if len(ordered) < 15:
        return TrendResult(vwap_indicator(ordered), None, None, None, None, None, None,
                           (None,) * len(ordered))
    tr, plus_dm, minus_dm = [], [], []
    for current, previous in zip(ordered[1:], ordered[:-1]):
        tr.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
        plus_dm.append(current.high - previous.high if current.high - previous.high > previous.low - current.low and current.high > previous.high else 0.0)
        minus_dm.append(previous.low - current.low if previous.low - current.low > current.high - previous.high and current.low < previous.low else 0.0)
    period = 14
    atr = sum(tr[-period:]) / period
    plus = sum(plus_dm[-period:]) / period
    minus = sum(minus_dm[-period:]) / period
    plus_di, minus_di = 100 * plus / atr if atr else 0.0, 100 * minus / atr if atr else 0.0
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if plus_di + minus_di else 0.0
    # A compact Wilder-style ADX series is sufficient for the signal and slope.
    adx_values = [dx]
    for index in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[index]) / period
        plus = (plus * (period - 1) + plus_dm[index]) / period
        minus = (minus * (period - 1) + minus_dm[index]) / period
        pdi, mdi = 100 * plus / atr if atr else 0.0, 100 * minus / atr if atr else 0.0
        adx_values.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    obv = [0.0]
    closes = [bar.close for bar in ordered]
    for index in range(1, len(ordered)):
        obv.append(obv[-1] + (ordered[index].volume if closes[index] > closes[index - 1] else -ordered[index].volume if closes[index] < closes[index - 1] else 0))
    rsi_history = rsi_series(closes)
    return TrendResult(vwap_indicator(ordered), adx_values[-1], plus_di, minus_di,
                       len(adx_values) > 1 and adx_values[-1] > adx_values[-2],
                       len(obv) >= 4 and obv[-1] > obv[-2] > obv[-3] > obv[-4],
                       rsi_history[-1], rsi_history)
