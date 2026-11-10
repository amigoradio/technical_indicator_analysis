"""Offline adapter used to verify actual spawned-process boundaries."""
import time

from stock_report.models import DailyBar


def fetch(stock, start, end):
    if stock.code == "600000":
        time.sleep(10)
    if stock.code == "600001":
        raise RuntimeError("secret-credential-must-not-appear")
    return [DailyBar(end, 10, 12, 9, 11, 100, 1100)]
