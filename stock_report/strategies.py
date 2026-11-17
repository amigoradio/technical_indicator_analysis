"""Strategies consume shared indicators without fetching market data."""

from dataclasses import dataclass

from .indicators import AlignmentResult, SarResult, TrendResult


@dataclass(frozen=True)
class DirectionSignal:
    action: str
    reason: str
    confidence: int = 0


def strategy_two(sar: SarResult, alignment: AlignmentResult, trend: TrendResult | None = None,
                 close: float | None = None, volume_above_ma120: bool | None = None) -> DirectionSignal:
    if trend is None:
        if sar.reversal == "翻红" and alignment.direction == "多头排列" and alignment.candle_position == "上方":
            return DirectionSignal("做多", "SAR翻红、多头排列、MA60向上、股价站上60日线", 4)
        if sar.reversal == "翻绿" and alignment.direction == "空头排列" and alignment.candle_position == "下方":
            return DirectionSignal("做空", "SAR翻绿、空头排列、MA60向下、股价跌破60日线", 4)
        return DirectionSignal("不操作", "基础条件未同时满足")
    long_base = sar.reversal == "翻红" and alignment.direction == "多头排列" and alignment.candle_position == "上方"
    short_base = sar.reversal == "翻绿" and alignment.direction == "空头排列" and alignment.candle_position == "下方"
    if trend.adx is None or trend.rsi is None:
        return DirectionSignal("不操作", "ADX或RSI数据不足")
    if long_base and (trend.adx <= 25 or trend.rsi < 40):
        return DirectionSignal("不操作", "做多无行情：ADX不足或RSI低于40")
    if short_base and (trend.adx <= 25 or trend.rsi > 60):
        return DirectionSignal("不操作", "做空无行情：ADX不足或RSI高于60")
    if not long_base and not short_base:
        return DirectionSignal("不操作", "SAR、均线排列或60日线条件未同时满足")
    long = long_base
    confidence = 4
    reasons = ["SAR翻红、多头排列、MA60拐头、股价站上60日线"] if long else ["SAR翻绿、空头排列、MA60拐头、股价跌破60日线"]
    if close is not None and trend.vwap is not None and ((long and close > trend.vwap) or (not long and close < trend.vwap)):
        confidence += 1; reasons.append("收盘价位于VWAP之上" if long else "收盘价位于VWAP之下")
    if trend.adx > 25 and trend.adx_rising and ((long and trend.plus_di > trend.minus_di) or (not long and trend.minus_di > trend.plus_di)):
        confidence += 1; reasons.append("ADX上行且方向指标确认")
    if volume_above_ma120:
        confidence += 1; reasons.append("量能超过MA120V")
    if trend.obv_rising_three_days:
        confidence += 1; reasons.append("连续3日OBV向上")
    if (long and 40 < trend.rsi < 70) or (not long and 50 <= trend.rsi <= 60):
        confidence += 1; reasons.append("RSI处于有效区间")
    return DirectionSignal("做多" if long else "做空", "；".join(reasons), min(5, confidence))
