from datetime import date

from ..models import DailyBar, Stock


def fetch(stock: Stock, start: date, end: date) -> list[DailyBar]:
    import baostock as bs

    if stock.market != "CN" or stock.exchange == "BJ" or stock.is_etf:
        raise ValueError("Baostock 不支持该市场或 ETF（仅支持沪深普通 A 股）")
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError("Baostock 登录失败")
    try:
        result = bs.query_history_k_data_plus(
            f"{stock.exchange.lower()}.{stock.code}",
            "date,open,high,low,close,volume,amount,tradestatus",
            start_date=start.isoformat(), end_date=end.isoformat(), frequency="d", adjustflag="3")
        bars = []
        while result.error_code == "0" and result.next():
            row = result.get_row_data()
            if row[7] == "1":
                bars.append(DailyBar(date.fromisoformat(row[0]),
                                     *(float(v) for v in row[1:7])))
        if result.error_code != "0":
            raise RuntimeError("Baostock 查询失败")
        return bars
    finally:
        bs.logout()
