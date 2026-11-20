from datetime import date
import logging

from ..models import DailyBar, Stock


def get_name(stock: Stock) -> str:
    import baostock as bs

    if bs.login().error_code != "0":
        return ""
    try:
        result = bs.query_stock_basic(code=f"{stock.exchange.lower()}.{stock.code}")
        if result.error_code == "0" and result.next():
            row = dict(zip(result.fields, result.get_row_data()))
            return row.get("code_name", "")
        return ""
    finally:
        bs.logout()


def fetch(stock: Stock, start: date, end: date, *, frequency: str = "daily") -> list[DailyBar]:
    import baostock as bs

    if stock.market != "CN" or stock.exchange == "BJ":
        raise ValueError("Baostock 不支持港股或北交所")
    symbol = f"{stock.exchange.lower()}.{stock.code}"
    logging.getLogger(__name__).info("Baostock query_history_k_data_plus code=%s adjustflag=3", symbol)
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError("Baostock 登录失败")
    try:
        weekly = frequency == "weekly"
        fields = "date,open,high,low,close,volume,amount"
        result = bs.query_history_k_data_plus(
            symbol,
            fields if weekly else fields + ",tradestatus",
            start_date=start.isoformat(), end_date=end.isoformat(),
            frequency="w" if weekly else "d", adjustflag="3")
        bars = []
        while result.error_code == "0" and result.next():
            row = result.get_row_data()
            if weekly or row[7] == "1":
                bars.append(DailyBar(date.fromisoformat(row[0]),
                                     *(float(v) for v in row[1:7])))
        if result.error_code != "0":
            raise RuntimeError("Baostock 查询失败")
        return bars
    finally:
        bs.logout()
