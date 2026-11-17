from datetime import date, timedelta
import unittest

from stock_report.analysis import analyze
from stock_report.indicators import AlignmentResult, SarResult, moving_average_alignment, sar_indicator
from stock_report.models import DailyBar
from stock_report.strategies import strategy_two


def bars_from(closes):
    return [DailyBar(date(2026, 1, 1) + timedelta(days=index), close,
                     close + 0.2, close - 0.2, close, 100)
            for index, close in enumerate(closes)]


class StrategyTwoTests(unittest.TestCase):
    def test_alignment_and_entire_candle(self):
        rising = bars_from([100 + index * 2 for index in range(70)])
        falling = bars_from([300 - index * 2 for index in range(70)])
        self.assertEqual(moving_average_alignment(rising), AlignmentResult("多头排列", "上方"))
        self.assertEqual(moving_average_alignment(falling), AlignmentResult("空头排列", "下方"))
        self.assertEqual(analyze(rising).alignment.direction, "多头排列")

    def test_adhesion_and_short_history(self):
        self.assertEqual(moving_average_alignment(bars_from([100 + index * 0.001 for index in range(70)])).direction, "均线粘合")
        self.assertEqual(moving_average_alignment(bars_from([100] * 60)).direction, "数据不足")

    def test_sar_reversals_in_both_directions(self):
        falling = [150 - index * 2 for index in range(20)]
        rising = [100 + index * 2 for index in range(20)]
        self.assertEqual(sar_indicator(bars_from(falling)).reversal, "无")
        self.assertEqual(sar_indicator(bars_from(falling + [160])).reversal, "翻红")
        self.assertEqual(sar_indicator(bars_from(rising + [80])).reversal, "翻绿")
        self.assertIsNone(sar_indicator(bars_from([100])).value)

    def test_strategy_requires_all_conditions(self):
        red = SarResult(90, "红色", "翻红")
        green = SarResult(110, "绿色", "翻绿")
        self.assertEqual(strategy_two(red, AlignmentResult("多头排列", "上方")).action, "做多")
        self.assertEqual(strategy_two(green, AlignmentResult("空头排列", "下方")).action, "做空")
        for alignment in (AlignmentResult("多头排列", "穿越均线组"),
                          AlignmentResult("均线粘合", "上方"), AlignmentResult("数据不足", "上方")):
            self.assertEqual(strategy_two(red, alignment).action, "不操作")
        self.assertEqual(strategy_two(SarResult(90, "红色", "无"), AlignmentResult("多头排列", "上方")).action, "不操作")
