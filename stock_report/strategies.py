"""Strategies consume shared indicators without fetching market data."""

from dataclasses import dataclass

from .indicators import AlignmentResult, SarResult


@dataclass(frozen=True)
class DirectionSignal:
    action: str
    reason: str


def strategy_two(sar: SarResult, alignment: AlignmentResult) -> DirectionSignal:
    if (sar.reversal == "翻红" and alignment.direction == "多头排列"
            and alignment.candle_position == "上方"):
        return DirectionSignal("做多", "SAR翻红、多头排列、MA60向上、K线在均线组上方")
    if (sar.reversal == "翻绿" and alignment.direction == "空头排列"
            and alignment.candle_position == "下方"):
        return DirectionSignal("做空", "SAR翻绿、空头排列、MA60向下、K线在均线组下方")
    return DirectionSignal("不操作", "SAR与均线条件未同时满足")
