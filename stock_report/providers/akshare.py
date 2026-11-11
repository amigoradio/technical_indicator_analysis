from datetime import date

from ..models import DailyBar, Stock


def fetch(stock: Stock, start: date, end: date) -> list[DailyBar]:
    import akshare as ak

    kwargs = dict(period="daily", start_date=start.strftime("%Y%m%d"),
                  end_date=end.strftime("%Y%m%d"), adjust="")
    if stock.market == "HK":
        api = ak.stock_hk_hist
        kwargs["symbol"] = stock.code[2:]
    elif stock.is_etf:
        # fund_etf_hist_em is required for ETFs; stock_zh_a_hist is the
        # ordinary-share endpoint and returns empty data for some ETFs.
        api = ak.fund_etf_hist_em
        kwargs["symbol"] = stock.code
    else:
        api = ak.stock_zh_a_hist
        kwargs["symbol"] = stock.code
    frame = api(**kwargs)
    if frame is None or frame.empty:
        return []
    # Eastmoney A-share history reports lots (100 shares); HK history reports shares.
    volume_scale = 1 if stock.market == "HK" else 100
    return [DailyBar(date.fromisoformat(str(row["日期"])[:10]),
                     *(float(row[key]) for key in ("开盘", "最高", "最低", "收盘")),
                     float(row["成交量"]) * volume_scale, float(row["成交额"]))
            for row in frame.to_dict("records")]
