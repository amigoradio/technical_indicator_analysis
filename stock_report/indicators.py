"""Reusable indicators without provider, strategy or delivery dependencies."""

from dataclasses import dataclass

from .models import DailyBar


@dataclass(frozen=True)
class SarResult:
    value: float | None
    direction: str
    reversal: str


@dataclass(frozen=True)
class AlignmentResult:
    direction: str
    candle_position: str


def sar_indicator(bars: list[DailyBar], step: float = 0.02, maximum: float = 0.2) -> SarResult:
    """Parabolic SAR; red means rising, green means falling, flip is today only."""
    if not 0 < step <= maximum:
        raise ValueError("SAR requires 0 < step <= maximum")
    ordered = sorted(bars, key=lambda bar: bar.date)
    if len(ordered) < 2:
        return SarResult(None, "数据不足", "无")
    rising = ordered[1].close >= ordered[0].close
    value = ordered[0].low if rising else ordered[0].high
    extreme = ordered[0].high if rising else ordered[0].low
    acceleration = step
    reversal = "无"
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
            else:
                value = candidate
                if bar.high > extreme:
                    extreme, acceleration = bar.high, min(maximum, acceleration + step)
        else:
            candidate = max(candidate, *(item.high for item in previous))
            if bar.high > candidate:
                rising, value, extreme, acceleration = True, min(extreme, bar.low), bar.high, step
                reversal = "翻红"
            else:
                value = candidate
                if bar.low < extreme:
                    extreme, acceleration = bar.low, min(maximum, acceleration + step)
    return SarResult(value, "红色" if rising else "绿色", reversal)


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
