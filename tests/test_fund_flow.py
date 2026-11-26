from datetime import date, datetime, time
from http.client import RemoteDisconnected
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests
from urllib3.exceptions import ProtocolError

from stock_report.data import FetchResult
from stock_report import fund_flow
from stock_report.fund_flow import _sessions, _summarize
from stock_report.models import DailyBar, Stock
from stock_report.report import render


class FundFlowTests(unittest.TestCase):
    sessions = [date(2026, 11, day) for day in (19, 20, 23, 24, 25)]
    target = sessions[-1]

    def test_window_uses_five_trading_days(self):
        dates = [date(2026, 11, day) for day in (18, 19, 20, 21, 22, 23, 24, 25)]
        calendar = pd.DataFrame({"calendar_date": dates, "is_trading_day": [1, 1, 1, 0, 0, 1, 1, 1]})
        with patch("stock_report.fund_flow.trading_calendar", return_value=calendar):
            self.assertEqual(_sessions("CN", self.target), self.sessions)

    def test_divergence_uses_first_and_last_close_in_same_window(self):
        for amount, first, last, streak, divergence in (
            (-10, 10, 12, "连续流出", "主力出货"),
            (10, 12, 10, "连续流入", "主力抄底"),
            (10, 10, 12, "连续流入", ""),
            (-10, 12, 10, "连续流出", ""),
            (-10, 10, 10, "连续流出", ""),
        ):
            with self.subTest(amount=amount, first=first, last=last):
                closes = dict(zip(self.sessions, (first, 13, 9, 11, last)))
                flow = _summarize(dict.fromkeys(self.sessions, amount), self.target, self.sessions, "CNY", closes)
                self.assertEqual((flow.streak, flow.divergence), (streak, divergence))

    def test_incomplete_or_mixed_flow_and_missing_closes(self):
        closes = dict.fromkeys(self.sessions, 10)
        closes[self.target] = 12
        for interrupted in (None, 0, 10):
            known = dict.fromkeys(self.sessions, -10)
            known[self.sessions[1]] = interrupted
            flow = _summarize(known, self.target, self.sessions, "CNY", closes)
            self.assertEqual((flow.streak, flow.divergence), ("", ""))
        del closes[self.sessions[2]]
        known = dict.fromkeys(self.sessions, -10)
        flow = _summarize(known, self.target, self.sessions, "CNY", closes)
        self.assertEqual((flow.streak, flow.divergence), ("连续流出", ""))
        self.assertIsNone(_summarize({self.target: 0}, self.target, self.sessions, "CNY", closes))

    def test_daily_email_displays_both_labels(self):
        for prices, amount, label, color in (
            ((12, 11, 13, 11, 10), 1, "主力抄底", "#d00000"),
            ((10, 11, 13, 11, 12), -1, "主力出货", "#008000"),
        ):
            with self.subTest(label=label):
                stock = Stock.parse("510300")
                closes = dict(zip(self.sessions, prices))
                bars = [DailyBar(day, price, price, price, price, 100) for day, price in closes.items()]
                flow = _summarize(dict.fromkeys(self.sessions, amount), self.target, self.sessions, "CNY", closes)
                _, text, html = render([FetchResult(stock, bars)], self.target, fund_flows={stock.code: flow})
                self.assertIn(f"（{flow.streak}、{label}）", text)
                self.assertIn(f"（{flow.streak}、<span style='color:{color}'>{label}</span>）", html)

    def test_fallback_only_for_missing_cn_stocks_and_spaced_by_five_minutes(self):
        missing = [Stock.parse("600519"), Stock.parse("000001")]
        present = Stock.parse("600000")
        zero = Stock.parse("600001")
        etf = Stock.parse("510300")
        hk = Stock.parse("hk700")
        stocks = [missing[0], present, zero, etf, hk, missing[1]]
        events = []

        def request(operation, timeout, *args):
            if operation is fund_flow._stock_history:
                if args[0] == present:
                    return {self.target: 12}
                if args[0] == zero:
                    return {self.target: 0}
                if args[0] == missing[0]:
                    return dict.fromkeys(self.sessions[:-1], -5)
            elif operation is fund_flow._stock_flow_fallback:
                events.append(args[0].code)
                return -8
            elif operation is _sessions:
                return self.sessions
            return None

        with (patch.dict("os.environ", {"FUND_FLOW_HISTORY_FILE": ""}),
              patch.object(fund_flow, "_request", side_effect=request),
              patch.object(fund_flow.longbridge, "configured", return_value=True),
              patch.object(fund_flow, "sleep", side_effect=events.append)):
            manual = fund_flow.collect_fund_flows(stocks, self.target, self.target, 10)
            self.assertEqual(events, [])
            self.assertEqual(set(manual), {present.code})
            scheduled = fund_flow.collect_fund_flows(stocks, self.target, self.target, 10, scheduled=True)
            self.assertEqual(events, [missing[0].code, 300, missing[1].code])
            self.assertEqual(scheduled[missing[0].code].streak, "连续流出")
            self.assertEqual(scheduled[missing[1].code].streak, "")

    @patch("stock_report.fund_flow.requests.get")
    def test_fallback_queries_exchange_and_code_and_validates_quote_date(self, get):
        stock = Stock.parse("000001")
        timestamp = datetime.combine(self.target, time(15), tzinfo=fund_flow.SHANGHAI).timestamp()
        response = get.return_value
        row = {"f12": stock.code, "f13": 0, "f124": timestamp, "f225": -42, "f62": -41}
        response.json.return_value = {"data": {"diff": [row]}}
        self.assertEqual(fund_flow._stock_flow_fallback(stock, self.target, 10), -42)
        self.assertEqual(get.call_args.kwargs["params"]["secids"], "0.000001")
        row["f124"] -= 86400
        self.assertIsNone(fund_flow._stock_flow_fallback(stock, self.target, 10))

    def test_worker_classifies_nested_disconnect_and_http_429(self):
        response = requests.Response()
        response.status_code = 429
        for error, reason in (
            (requests.ConnectionError(ProtocolError("aborted", RemoteDisconnected())), "RemoteDisconnected"),
            (requests.HTTPError(response=response), "HTTP 429"),
        ):
            with self.subTest(reason=reason):
                connection = Mock()
                fund_flow._worker(connection, Mock(side_effect=error), ())
                connection.send.assert_called_once_with(("blocked", reason))

    def test_akshare_empty_frame_and_http_429_stop_immediately(self):
        from akshare.utils import http

        with self.assertRaises(fund_flow._InterfaceBlocked):
            fund_flow._akshare_flow_frame(pd.DataFrame, "diff")

        response = requests.Response()
        response.status_code = 429
        with patch.object(http, "request_with_retry", return_value=response) as request:
            with self.assertRaises(requests.HTTPError):
                fund_flow._akshare_flow_frame(lambda: http.request_with_retry("get", "test"), "diff")
            request.assert_called_once_with("get", "test", max_retries=1)

    def test_blocked_endpoints_stop_for_remaining_stocks_without_waiting(self):
        stocks = [Stock.parse(code) for code in ("600519", "000001", "600000", "600001")]
        queries = []

        def request(operation, timeout, *args):
            if operation in (fund_flow._stock_history, fund_flow._stock_flow_fallback):
                queries.append((operation, args[0]))
            if operation is fund_flow._stock_history and args[0] == stocks[0]:
                return {self.target: 12}
            if operation is _sessions:
                return self.sessions
            raise fund_flow._InterfaceBlocked("empty data")

        with (patch.object(fund_flow, "_request", side_effect=request),
              patch.object(fund_flow, "sleep") as wait):
            flows = fund_flow.collect_fund_flows(stocks, self.target, self.target, 10, scheduled=True)
        self.assertEqual(queries, [
            (fund_flow._stock_history, stocks[0]),
            (fund_flow._stock_history, stocks[1]),
            (fund_flow._stock_flow_fallback, stocks[1]),
        ])
        self.assertEqual(set(flows), {stocks[0].code})
        wait.assert_not_called()
