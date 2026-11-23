from datetime import date, timedelta
import unittest

from stock_report.data import FetchResult
from stock_report.indicators import nine_turn_streak
from stock_report.models import DailyBar, Stock
from stock_report.report import render


class NineTurnTests(unittest.TestCase):
    def test_counts_consecutive_four_bar_comparisons(self):
        # Each four-bar comparison is higher, despite declines between neighbors.
        closes = [40, 30, 20, 10, 41, 31, 21, 11, 42, 32, 22, 12, 43, 33]
        for direction in (1, -1):
            prices = [100 + direction * close for close in closes]
            for size, expected in ((4, 0), (5, 1), (12, 8), (13, 9), (14, 10)):
                with self.subTest(direction=direction, size=size):
                    self.assertEqual(nine_turn_streak(prices[:size]), direction * expected)

    def test_equal_and_opposite_comparisons_reset_the_streak(self):
        closes = list(range(10, 23))
        equal, opposite = closes[-4], closes[-4] - 1
        self.assertEqual(nine_turn_streak(closes + [equal]), 0)
        self.assertEqual(nine_turn_streak(closes + [opposite]), -1)
        self.assertEqual(nine_turn_streak(closes + [equal, 24]), 1)
        self.assertEqual(nine_turn_streak(closes + [opposite, 24]), 1)

    def test_report_shows_only_the_latest_completed_daily_signal(self):
        stock = Stock.parse("600519")
        first = date(2026, 1, 1)
        for closes, frequency, expected in (
            (range(10, 22), "daily", None),
            (range(10, 23), "daily", "达成上涨九转"),
            (range(30, 16, -1), "daily", "达成下跌九转"),
            ([*range(10, 23), 19], "daily", None),
            (range(10, 23), "weekly", None),
        ):
            with self.subTest(closes=closes, frequency=frequency):
                days_per_bar = 7 if frequency == "weekly" else 1
                bars = [
                    DailyBar(first + timedelta(days=i * days_per_bar),
                             close, close + 1, close - 1, close, 100)
                    for i, close in enumerate(closes)
                ]
                result = FetchResult(stock, bars, "Fake")
                _, body, html = render([result], bars[-1].date, frequency=frequency)
                for label in ("达成上涨九转", "达成下跌九转"):
                    self.assertEqual(label in body.splitlines(), label == expected)
                    self.assertEqual(f"<p>{label}</p>" in html, label == expected)
