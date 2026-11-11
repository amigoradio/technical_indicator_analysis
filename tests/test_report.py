from datetime import date, datetime, timedelta
from email import policy
import base64
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from stock_report.analysis import analyze
from stock_report.config import Settings
from stock_report.data import DataService, FetchResult, Provider, provider_chain, request
from stock_report.email import send
from stock_report.models import DailyBar, Stock, validate_bars
from stock_report.providers import akshare, baostock, longbridge
from stock_report.report import render
from stock_report.__main__ import main

TARGET = date(2026, 11, 9)
ENV = {"STOCK_LIST": "600519;hk700;600519", "EMAIL_SENDER": "sender@qq.com",
       "EMAIL_PASSWORD": "test-only-password"}


def bar(day=TARGET, close=11):
    return DailyBar(day, 10, max(12, close), 9, close, 100, 1100)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.settings = Settings.from_env()

    def test_settings_defaults_and_deduplication(self):
        self.assertEqual([s.code for s in self.settings.stocks], ["600519", "hk00700"])
        self.assertEqual(self.settings.receivers, ("sender@qq.com",))
        self.assertEqual(self.settings.sender_name, "可爱炒股小秘")
        self.assertNotIn("test-only-password", repr(self.settings))

    def test_receivers_and_blank_optional_secrets(self):
        os.environ.update(EMAIL_RECEIVERS="a@qq.com, b@163.com,a@qq.com",
                          EMAIL_SMTP_PORT="", DATA_REQUEST_TIMEOUT="")
        self.assertEqual(Settings.from_env().receivers, ("a@qq.com", "b@163.com"))
        self.assertEqual(Settings.from_env().request_timeout, 60)

    def test_lowercase_stock_list(self):
        del os.environ["STOCK_LIST"]
        os.environ["stock_list"] = "000001;hk00005"
        self.assertEqual(len(Settings.from_env().stocks), 2)

    def test_invalid_settings(self):
        for key, value in (("EMAIL_PASSWORD", ""),
                           ("EMAIL_RECEIVERS", "evil\r\nBcc:x@qq.com")):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_invalid_stock_is_reported_while_valid_stocks_continue(self):
        os.environ["STOCK_LIST"] = "600519;bad;159870"
        settings = Settings.from_env()
        self.assertEqual([s.code for s in settings.stocks], ["600519", "159870"])
        self.assertEqual(len(settings.config_errors), 1)

    def test_invalid_optional_config_is_reported_with_default(self):
        os.environ["DATA_RETRY_ATTEMPTS"] = "0"
        settings = Settings.from_env()
        self.assertEqual(settings.attempts, 2)
        self.assertTrue(settings.config_errors)

    def test_market_symbols(self):
        for code, symbol in (("600519", "600519.SH"), ("000001", "000001.SZ"),
                              ("920001", "920001.BJ"), ("511090", "511090.SH"),
                              ("159870", "159870.SZ"), ("562950", "562950.SH"),
                              ("HK700", "00700.HK")):
            self.assertEqual(Stock.parse(code).longbridge_symbol, symbol)

    def test_etf_marker_disables_stock_fundamentals_and_adjustment(self):
        for code in ("511090", "159870", "562950"):
            instrument = Stock.parse(code)
            self.assertEqual(instrument.asset_type, "ETF")
            self.assertFalse(instrument.supports_fundamentals)
            self.assertEqual(instrument.adjustment, "none")
        stock = Stock.parse("600519")
        self.assertEqual(stock.asset_type, "STOCK")
        self.assertTrue(stock.supports_fundamentals)
        self.assertEqual(stock.adjustment, "none")

    def test_provider_routing(self):
        self.assertEqual([p.name for p in provider_chain(Stock.parse("600519"))], ["Akshare", "Baostock"])
        self.assertEqual([p.name for p in provider_chain(Stock.parse("511090"))], ["Akshare", "Baostock"])
        os.environ["LONGBRIDGE_APP_KEY"] = "configured"
        self.assertEqual([p.name for p in provider_chain(Stock.parse("hk700"))], ["Longbridge", "Akshare"])
        self.assertEqual(provider_chain(Stock.parse("600519"))[0].name, "Longbridge")

    def test_validation_rejects_old_empty_corrupt_and_duplicate_bars(self):
        for bars in ([], [bar(TARGET - timedelta(days=1))], [bar(close=float("nan"))],
                     [bar(), bar()], [DailyBar(TARGET, 10, 9, 8, 11, 1)]):
            with self.subTest(bars=bars), self.assertRaises(ValueError):
                validate_bars(bars, TARGET)

    def test_analysis_is_sorted_and_handles_short_history(self):
        result = analyze([bar(), bar(TARGET - timedelta(days=1), 10)])
        self.assertAlmostEqual(result.change_pct, 10)
        self.assertIsNone(result.moving_averages[5])
        self.assertIsNone(analyze([bar()]).change_pct)

    def test_moving_average(self):
        bars = [bar(TARGET - timedelta(days=i), 10 + i) for i in range(20)]
        result = analyze(bars)
        self.assertEqual(result.moving_averages, {5: 12, 10: 14.5, 20: 19.5})

    @patch("stock_report.data.time.sleep")
    @patch("stock_report.data.request")
    def test_stale_akshare_falls_back_to_baostock(self, fetch, sleep):
        fetch.side_effect = [[bar(TARGET - timedelta(days=1))], TimeoutError("timeout"), [bar()]]
        result = DataService(self.settings).fetch(Stock.parse("600519"), TARGET)
        self.assertEqual(result.source, "Baostock")
        self.assertEqual([call.args[0].name for call in fetch.call_args_list], ["Akshare", "Akshare", "Baostock"])

    @patch("stock_report.data.time.sleep")
    @patch("stock_report.data.request", side_effect=TimeoutError("timeout"))
    def test_hk_failure_never_calls_baostock(self, fetch, sleep):
        result = DataService(self.settings).fetch(Stock.parse("hk700"), TARGET)
        self.assertFalse(result.bars)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("未能获得数据", render([result], TARGET)[1])

    @patch("stock_report.data.time.sleep")
    @patch("stock_report.data.request")
    def test_longbridge_success_short_circuits_fallback(self, fetch, sleep):
        os.environ["LONGBRIDGE_APP_KEY"] = "configured"
        fetch.return_value = [bar()]
        result = DataService(self.settings).fetch(Stock.parse("hk700"), TARGET)
        self.assertEqual(result.source, "Longbridge")
        self.assertEqual(fetch.call_count, 1)

    def test_process_success_timeout_and_exception_redaction(self):
        provider = Provider("Fake", "tests.fake_provider", ("CN",))
        self.assertEqual(request(provider, Stock.parse("600519"), TARGET, TARGET, 10), [bar()])
        with self.assertRaises(TimeoutError):
            request(provider, Stock.parse("600000"), TARGET, TARGET, 0.1)
        with self.assertRaises(RuntimeError) as caught:
            request(provider, Stock.parse("600001"), TARGET, TARGET, 10)
        self.assertNotIn("secret-credential", str(caught.exception))
        self.assertEqual(multiprocessing.active_children(), [])

    def test_akshare_unit_normalization(self):
        sdk = MagicMock()
        frame = MagicMock()
        frame.empty = False
        frame.to_dict.return_value = [{"日期": str(TARGET), "开盘": 10, "最高": 12,
                                       "最低": 9, "收盘": 11, "成交量": 100, "成交额": 1100}]
        sdk.stock_zh_a_hist.return_value = sdk.stock_hk_hist.return_value = frame
        with patch.dict("sys.modules", {"akshare": sdk}):
            self.assertEqual(akshare.fetch(Stock.parse("600519"), TARGET, TARGET)[0].volume, 10000)
            self.assertEqual(akshare.fetch(Stock.parse("hk700"), TARGET, TARGET)[0].volume, 100)
        self.assertEqual(sdk.stock_zh_a_hist.call_args.kwargs["adjust"], "")

    def test_akshare_etf_uses_etf_history_endpoint(self):
        sdk = MagicMock()
        frame = MagicMock(empty=False)
        frame.to_dict.return_value = [{"日期": str(TARGET), "开盘": 10, "最高": 12,
                                       "最低": 9, "收盘": 11, "成交量": 100, "成交额": 1100}]
        sdk.fund_etf_hist_em.return_value = frame
        with patch.dict("sys.modules", {"akshare": sdk}):
            bars = akshare.fetch(Stock.parse("511090"), TARGET, TARGET)
        sdk.fund_etf_hist_em.assert_called_once()
        sdk.stock_zh_a_hist.assert_not_called()
        self.assertEqual(bars[0].volume, 10000)

    def test_akshare_etf_uses_sina_when_eastmoney_is_rate_limited(self):
        sdk = MagicMock()
        sdk.fund_etf_hist_em.side_effect = RuntimeError("Push2RateLimitError")
        frame = MagicMock(empty=False)
        frame.to_dict.return_value = [{"date": str(TARGET), "open": 10, "high": 12,
                                       "low": 9, "close": 11, "volume": 100, "amount": 1100}]
        sdk.fund_etf_hist_sina.return_value = frame
        with patch.dict("sys.modules", {"akshare": sdk}):
            bars = akshare.fetch(Stock.parse("511090"), TARGET, TARGET)
        sdk.fund_etf_hist_sina.assert_called_once_with(symbol="sh511090")
        self.assertEqual(bars[0].close, 11)

    def test_baostock_filters_suspension_and_logs_out(self):
        sdk = MagicMock()
        sdk.login.return_value.error_code = "0"
        result = sdk.query_history_k_data_plus.return_value
        result.error_code = "0"
        result.next.side_effect = [True, True, False]
        result.get_row_data.side_effect = [[str(TARGET), "10", "12", "9", "11", "100", "1100", "0"],
                                           [str(TARGET), "10", "12", "9", "11", "100", "1100", "1"]]
        with patch.dict("sys.modules", {"baostock": sdk}):
            self.assertEqual(baostock.fetch(Stock.parse("600519"), TARGET, TARGET), [bar()])
        sdk.logout.assert_called_once()
        self.assertEqual(sdk.query_history_k_data_plus.call_args.kwargs["adjustflag"], "3")

    def test_longbridge_empty_urls_and_legacy_credentials(self):
        sdk = MagicMock()
        os.environ.update(LONGBRIDGE_APP_KEY="key", LONGBRIDGE_APP_SECRET="secret",
                          LONGBRIDGE_ACCESS_TOKEN="token", LONGBRIDGE_HTTP_URL="",
                          LONGBRIDGE_REGION="hk")
        with patch.dict("sys.modules", {"longbridge.openapi": sdk}):
            longbridge.build_config()
        self.assertEqual(os.environ["LONGPORT_REGION"], "hk")
        self.assertEqual(sdk.Config.from_apikey.call_args.args, ("key", "secret", "token"))
        self.assertEqual(sdk.Config.from_apikey.call_args.kwargs["http_url"], "https://openapi.longbridge.com")
        self.assertFalse(sdk.Config.from_apikey.call_args.kwargs["enable_print_quote_packages"])

    def test_oauth_never_launches_interactive_browser(self):
        with self.assertRaises(RuntimeError):
            longbridge.reject_interactive_auth("https://secret-credential")

    def test_oauth_restores_cache_and_reuses_refreshed_token(self):
        sdk = MagicMock()
        original = b'{"test_token":"old"}'
        refreshed = b'{"test_token":"refreshed"}'
        os.environ.update(LONGBRIDGE_OAUTH_CLIENT_ID="test-client",
                          LONGBRIDGE_OAUTH_TOKEN_CACHE_B64=base64.b64encode(original).decode())
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / ".longbridge/openapi/tokens/test-client"
            with patch.object(longbridge.Path, "home", return_value=Path(directory)), \
                    patch.dict("sys.modules", {"longbridge.openapi": sdk}):
                longbridge.build_config()
                self.assertEqual(cache.read_bytes(), original)
                cache.write_bytes(refreshed)
                longbridge.build_config()
                self.assertEqual(cache.read_bytes(), refreshed)
        sdk.OAuthBuilder.return_value.build.assert_called_with(longbridge.reject_interactive_auth)
        self.assertEqual(sdk.Config.from_oauth.call_count, 2)

    def test_oauth_failure_falls_back_to_complete_legacy(self):
        sdk = MagicMock()
        os.environ.update(LONGBRIDGE_OAUTH_CLIENT_ID="bad/client", LONGBRIDGE_APP_KEY="key",
                          LONGBRIDGE_APP_SECRET="secret", LONGBRIDGE_ACCESS_TOKEN="token")
        with patch.dict("sys.modules", {"longbridge.openapi": sdk}):
            longbridge.build_config()
        sdk.Config.from_apikey.assert_called_once()
        sdk.OAuthBuilder.assert_not_called()

    def test_baostock_excludes_hk_and_beijing(self):
        with patch.dict("sys.modules", {"baostock": MagicMock()}):
            for code in ("hk700", "920001"):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    baostock.fetch(Stock.parse(code), TARGET, TARGET)

    def test_report_escapes_html_and_keeps_success_and_failure(self):
        subject, text, html = render([FetchResult(Stock.parse("600519"), [bar()], "Akshare"),
                                     FetchResult(Stock.parse("hk700"), [], error="<bad>")], TARGET)
        self.assertIn("收盘: 11.00", text)
        self.assertIn("未能获得数据", html)
        self.assertIn("&lt;bad&gt;", html)
        self.assertNotIn("<bad>", html)

    @patch("stock_report.email.smtplib.SMTP_SSL")
    def test_mail_has_chinese_name_and_multipart_body(self, smtp):
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {}
        send(self.settings, *render([FetchResult(Stock.parse("600519"), [bar()], "Akshare")], TARGET))
        message = server.send_message.call_args.args[0]
        self.assertIn("可爱炒股小秘", str(message["From"]))
        self.assertEqual(len(message.get_payload()), 2)
        self.assertIn(b"multipart/alternative", message.as_bytes(policy=policy.SMTP))
        server.login.assert_called_once_with("sender@qq.com", "test-only-password")

    @patch("stock_report.email.smtplib.SMTP_SSL")
    def test_mail_headers_encode_chinese_sender_and_subject(self, smtp):
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {}
        send(self.settings, "股票日 K 收盘报告", "正文", "<p>正文</p>")
        message = server.send_message.call_args.args[0]
        wire = message.as_bytes()
        self.assertIn(b"=?utf-8?", wire.lower())
        self.assertIn(b"smtp.qq.com", str(message).encode()) if False else None

    @patch("stock_report.email.smtplib.SMTP")
    def test_mail_starttls_and_partial_refusal(self, smtp):
        os.environ["EMAIL_SENDER"] = "sender@gmail.com"
        settings = Settings.from_env()
        server = smtp.return_value.__enter__.return_value
        server.send_message.return_value = {"receiver": (550, b"no")}
        with self.assertRaises(RuntimeError):
            send(settings, "subject", "text", "<p>html</p>")
        server.starttls.assert_called_once()

    @patch("stock_report.__main__.send")
    @patch("stock_report.__main__.DataService")
    @patch("stock_report.__main__.datetime")
    def test_pipeline_sends_even_if_all_data_missing(self, clock, service, mail):
        clock.now.return_value = datetime(2026, 11, 9, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        service.return_value.fetch.side_effect = [FetchResult(s, [], error="无数据") for s in self.settings.stocks]
        self.assertEqual(main(), 0)
        mail.assert_called_once()

    @patch("stock_report.__main__.send")
    @patch("stock_report.__main__.DataService")
    @patch("stock_report.__main__.datetime")
    def test_pipeline_partial_success_still_sends(self, clock, service, mail):
        clock.now.return_value = datetime(2026, 11, 9, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        service.return_value.fetch.side_effect = [FetchResult(self.settings.stocks[0], [bar()], "Akshare"),
                                                 FetchResult(self.settings.stocks[1], [], error="无数据")]
        self.assertEqual(main(), 0)
        mail.assert_called_once()

    @patch("stock_report.__main__.send")
    @patch("stock_report.__main__.DataService")
    @patch("stock_report.__main__.datetime")
    def test_weekend_and_before_close_use_previous_session(self, clock, service, mail):
        service.return_value.fetch.return_value = FetchResult(self.settings.stocks[0], [], error="无数据")
        clock.now.return_value = datetime(2026, 11, 8, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(main(), 0)
        mail.reset_mock()
        clock.now.return_value = datetime(2026, 11, 9, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(main(), 0)
        mail.assert_called_once()
        self.assertEqual(service.return_value.fetch.call_args.args[1], date(2026, 11, 6))

    @patch("stock_report.__main__.send")
    @patch("stock_report.__main__.DataService")
    @patch("stock_report.__main__.datetime")
    def test_force_run_bypasses_time_checks_and_labels_email(self, clock, service, mail):
        for now in (datetime(2026, 11, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
                    datetime(2026, 11, 9, 6, tzinfo=ZoneInfo("Asia/Shanghai"))):
            with self.subTest(now=now):
                clock.now.return_value = now
                service.return_value.fetch.side_effect = [
                    FetchResult(stock, [bar(now.date())], "Akshare") for stock in self.settings.stocks]
                self.assertEqual(main(["--force-run"]), 0)
                args = mail.call_args.args
                self.assertTrue(args[1].startswith("[手动测试]"))
                self.assertIn("手动测试", args[2])
                self.assertIn("手动测试", args[3])

    @patch("stock_report.__main__.send")
    @patch("stock_report.__main__.DataService")
    @patch("stock_report.__main__.datetime")
    def test_force_run_still_reports_missing_today(self, clock, service, mail):
        clock.now.return_value = datetime(2026, 11, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
        service.return_value.fetch.side_effect = [FetchResult(s, [], error="无数据") for s in self.settings.stocks]
        self.assertEqual(main(["--force-run"]), 0)
        mail.assert_called_once()
        self.assertIn("未能获得数据", mail.call_args.args[2])

    @patch("stock_report.__main__.render", side_effect=RuntimeError("formatter failure"))
    @patch("stock_report.__main__.send")
    @patch("stock_report.__main__.DataService")
    @patch("stock_report.__main__.datetime")
    def test_report_failure_still_reaches_email(self, clock, service, mail, render):
        clock.now.return_value = datetime(2026, 11, 9, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        service.return_value.fetch.return_value = FetchResult(self.settings.stocks[0], [], error="无数据")
        self.assertEqual(main(), 0)
        mail.assert_called_once()
        self.assertIn("报告生成失败", mail.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
