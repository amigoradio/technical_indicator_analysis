from dataclasses import replace
from datetime import date, timedelta
import unittest

from stock_report.analysis import analyze
from stock_report.data import FetchResult
from stock_report.indicators import price_divergences, rsi_series
from stock_report.models import DailyBar, Stock
from stock_report.report import render


def bars_from(closes):
    return [DailyBar(date(2026, 1, 1) + timedelta(days=index), price,
                     price + 0.2, price - 0.2, price, 100)
            for index, price in enumerate(closes)]


class DivergenceTests(unittest.TestCase):
    def test_matching_price_pivots_and_confirmation(self):
        closes = [10, 11, 14, 11, 10, 11, 15, 11, 10]
        bars = bars_from(closes)
        indicator = (None, 0, 5, 1, 0, 1, 4, 1, 0)
        self.assertEqual(price_divergences(bars, indicator), ("顶背离",))
        self.assertEqual(price_divergences(bars[:-1], indicator[:-1]), ())
        self.assertEqual(price_divergences(bars, tuple(closes)), ())
        self.assertEqual(price_divergences(bars, (None,) * len(bars)), ())

    def test_rsi_series_warmup_and_flat_prices(self):
        self.assertEqual(rsi_series([10] * 16), (None,) * 14 + (50.0, 50.0))

    def test_macd_marks_second_matching_cross_in_both_email_formats(self):
        closes = ([100 + index for index in range(40)]
                  + [135 - 4 * index for index in range(9)]
                  + [140, 101, 140, 95])
        for prices, extreme, label, cross in (
            (closes, "high", "顶背离", "死叉"),
            ([300 - price for price in closes], "low", "底背离", "金叉"),
        ):
            with self.subTest(label=label):
                bars = bars_from(prices)
                bars[46] = replace(bars[46], **{extreme: 150})
                self.assertEqual(analyze(bars[:-4]).macd_divergences, (label,))
                self.assertEqual(analyze(bars[:-2]).macd_cross, cross)
                self.assertEqual(analyze(bars[:-2]).macd_divergences, (label,))
                self.assertEqual(analyze(bars[:-1]).macd_divergences, (label,))
                result = analyze(bars)
                self.assertEqual(result.macd_cross, cross)
                self.assertEqual(result.macd_divergences, (label + "*",))
                self.assertEqual(result.rsi_divergences, (label,))
                stock = FetchResult(Stock.parse("511090"), bars, "Fake")
                _, body, html = render([stock], bars[-1].date)
                self.assertIn(f"MACD 与价格：{label}*", body)
                self.assertIn(f"<p>MACD 与价格：{label}*</p>", html)

    def test_real_indicators_and_both_email_formats(self):
        closes = [100 + index for index in range(40)] + [135 - 4 * index for index in range(11)]
        top = bars_from(closes)
        top[46] = replace(top[46], high=150)
        top[-1] = replace(top[-1], instrument_name="<标的>")
        bottom = bars_from([300 - price for price in closes])
        bottom[46] = replace(bottom[46], low=150)
        for bars, label in ((top, "顶背离"), (bottom, "底背离")):
            with self.subTest(label=label):
                result = analyze(bars)
                self.assertEqual(result.macd_divergences, (label,))
                self.assertEqual(result.rsi_divergences, (label,))

        results = [
            FetchResult(Stock.parse("511090"), top, "Fake"),
            FetchResult(Stock.parse("hk00700"), bottom, "Fake"),
            FetchResult(Stock.parse("600519"), bars_from([10] * 51), "Fake"),
            FetchResult(Stock.parse("000001"), bars_from([10]), "Fake"),
        ]
        _, body, html = render(results, top[-1].date)
        self.assertIn("代码 | 名称 | 涨跌情况 | 背离情况 | 策略一", body)
        self.assertIn("<th>名称</th><th>涨跌情况</th><th>背离情况</th>", html)
        self.assertIn("511090 | <标的> | -4.04% | 背离", body)
        self.assertIn("<td>&lt;标的&gt;</td><td>-4.04%</td><td>背离</td>", html)
        self.assertIn("hk00700 | 名称暂缺 | +1.99% | 背离", body)
        self.assertIn("600519 | 名称暂缺 | 0.00% | 无", body)
        self.assertIn("000001 | 名称暂缺 | — | 无", body)
        for output in (body, html):
            self.assertIn("MACD 与价格：顶背离", output)
            self.assertIn("RSI 与价格：底背离", output)
            self.assertEqual(output.count("MACD 与价格："), 2)
            self.assertEqual(output.count("RSI 与价格："), 2)
            self.assertEqual(output.count("RSI(14) 超买"), 1)
            self.assertEqual(output.count("RSI(14) 超卖"), 1)
