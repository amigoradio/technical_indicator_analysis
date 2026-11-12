"""Provider-independent indicators and the first trading strategy."""

from dataclasses import dataclass
from .models import DailyBar


@dataclass(frozen=True)
class MacdResult:
    macd: float | None
    signal: float | None
    histogram: float | None
    crossover: str
    zone: str


@dataclass(frozen=True)
class StrategyResult:
    long_score: int
    short_score: int
    long_reasons: tuple[str, ...]
    short_reasons: tuple[str, ...]


@dataclass(frozen=True)
class Analysis:
    latest: DailyBar
    change_pct: float | None
    moving_averages: dict[int, float | None]
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    macd_crossover: str
    macd_zone: str
    range_days: int
    range_percentile: float | None
    range_position: str
    volume_ma120: float | None
    volume_above_ma120: bool | None
    volume_above_ma120_three_days: bool | None
    long_score: int
    short_score: int
    long_reasons: tuple[str, ...]
    short_reasons: tuple[str, ...]


def moving_average(values: list[float], period: int) -> float | None:
    """Return the latest simple moving average, or None if history is short."""
    return sum(values[-period:]) / period if len(values) >= period else None


def ema_series(values: list[float], period: int) -> list[float | None]:
    if len(values) < period:
        return [None] * len(values)
    result: list[float | None] = [None] * (period - 1)
    current = sum(values[:period]) / period
    result.append(current)
    alpha = 2 / (period + 1)
    for value in values[period:]:
        current = value * alpha + current * (1 - alpha)
        result.append(current)
    return result


def macd_indicator(closes: list[float]) -> MacdResult:
    fast, slow = ema_series(closes, 12), ema_series(closes, 26)
    macd_values = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow)]
    compact = [value for value in macd_values if value is not None]
    signal_compact = ema_series(compact, 9)
    signals = [None] * (len(macd_values) - len(signal_compact)) + signal_compact
    macd, signal = macd_values[-1], signals[-1]
    histogram = macd - signal if macd is not None and signal is not None else None
    crossover = "无"
    if macd is not None and signal is not None and len(macd_values) > 1:
        previous_macd, previous_signal = macd_values[-2], signals[-2]
        if previous_macd is not None and previous_signal is not None:
            if previous_macd <= previous_signal < macd:
                crossover = "金叉"
            elif previous_macd >= previous_signal > macd:
                crossover = "死叉"
    zone = "0轴上方" if macd is not None and macd >= 0 else "0轴下方" if macd is not None else "未知"
    return MacdResult(macd, signal, histogram, crossover, zone)


def volume_average(volumes: list[float], period: int, *, exclude_latest: bool = True) -> float | None:
    values = volumes[:-1] if exclude_latest else volumes
    # If a listing has fewer historical sessions, use every available session
    # rather than fabricating a missing value. Suspended days are absent from
    # provider responses and therefore naturally excluded.
    return sum(values[-period:]) / min(period, len(values)) if values else None


def _position_thresholds(days: int) -> tuple[float, float]:
    if days <= 20:
        low = 20.0
    elif days <= 60:
        low = 20.0 + (days - 20) * -2.0 / 40
    else:
        low = 18.0 + (days - 60) * -3.0 / 60
    return low, 100.0 - low


def price_position(closes: list[float], days: int) -> tuple[float | None, str]:
    if not 10 <= days <= 360:
        raise ValueError("range_days 必须在 10 到 360 之间")
    window = closes[-days:]
    if len(window) < days or max(window) == min(window):
        return None, "数据不足"
    percentile = (window[-1] - min(window)) / (max(window) - min(window)) * 100
    low, high = _position_thresholds(days)
    return percentile, "底部" if percentile <= low else "顶部" if percentile >= high else "区间中部"


def strategy_one(bars: list[DailyBar], averages: dict[int, float | None], macd: MacdResult,
                 position: str) -> StrategyResult:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    ma5, previous_ma5 = averages[5], moving_average(closes[:-1], 5)
    if ma5 is None or previous_ma5 is None:
        return StrategyResult(0, 0, (), ())
    bullish = macd.crossover == "金叉" and closes[-1] > ma5 and ma5 > previous_ma5
    bearish = macd.crossover == "死叉" and closes[-1] < ma5 and ma5 < previous_ma5
    long_reasons: list[str] = ["金叉+收盘价高于MA5+MA5上行"] if bullish else []
    short_reasons: list[str] = ["死叉+收盘价低于MA5+MA5下行"] if bearish else []
    if bullish:
        if len(volumes) > 1 and volumes[-1] > volumes[-2]: long_reasons.append("量能高于上一日")
        average120 = volume_average(volumes, 120)
        if average120 is not None and volumes[-1] > average120: long_reasons.append("量能高于前120日均量")
        if closes[-1] > ma5 * 1.02: long_reasons.append("收盘价明显高于MA5")
        if ma5 > previous_ma5 * 1.01: long_reasons.append("MA5明显上行")
        if position == "底部": long_reasons.append("处于区间底部")
    if bearish:
        if len(volumes) > 1 and volumes[-1] > volumes[-2]: short_reasons.append("量能高于上一日")
        if closes[-1] < ma5 * 0.98: short_reasons.append("收盘价明显低于MA5")
        if sum(closes[-1] < value for value in averages.values() if value is not None) >= 2: short_reasons.append("收盘价低于多条均线")
        if ma5 < previous_ma5 * 0.99: short_reasons.append("MA5明显下行")
        if position == "顶部": short_reasons.append("处于区间顶部")
    return StrategyResult(min(5, len(long_reasons)) if bullish else 0, min(5, len(short_reasons)) if bearish else 0,
                          tuple(long_reasons), tuple(short_reasons))


def analyze(bars: list[DailyBar], range_days: int = 60) -> Analysis:
    if not bars:
        raise ValueError("分析需要至少一根日 K")
    ordered = sorted(bars, key=lambda bar: bar.date)
    latest, closes = ordered[-1], [bar.close for bar in ordered]
    averages = {period: moving_average(closes, period) for period in (5, 10, 20)}
    macd = macd_indicator(closes)
    percentile, position = price_position(closes, range_days)
    strategy = strategy_one(ordered, averages, macd, position)
    volumes = [bar.volume for bar in ordered]
    comparisons = [volumes[index] > volume_average(volumes[:index + 1], 120)
                   for index in range(max(1, len(volumes) - 3), len(volumes))
                   if volume_average(volumes[:index + 1], 120) is not None]
    above_today = (volumes[-1] > volume_average(volumes, 120)
                   if volume_average(volumes, 120) is not None else None)
    above_three = all(comparisons) if len(comparisons) == 3 else None
    previous = ordered[-2].close if len(ordered) > 1 else None
    return Analysis(latest, (latest.close / previous - 1) * 100 if previous else None, averages,
                    macd.macd, macd.signal, macd.histogram,
                    f"{macd.zone}{macd.crossover}" if macd.crossover != "无" else "无", macd.zone,
                    range_days, percentile, position, volume_average(volumes, 120), above_today, above_three,
                    strategy.long_score, strategy.short_score, strategy.long_reasons, strategy.short_reasons)
