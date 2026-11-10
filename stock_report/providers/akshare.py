from datetime import date

from ..models import DailyBar, Stock


def fetch(stock: Stock, start: date, end: date) -> list[DailyBar]:
    import akshare as ak

    api = ak.stock_hk_hist if stock.market == "HK" else ak.stock_zh_a_hist
    frame = api(symbol=stock.code[2:] if stock.market == "HK" else stock.code,
                period="daily", start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"), adjust="")
    if frame is None or frame.empty:
        return []
    # Eastmoney A-share history reports lots (100 shares); HK history reports shares.
    volume_scale = 1 if stock.market == "HK" else 100
    return [DailyBar(date.fromisoformat(str(row["日期"])[:10]),
                     *(float(row[key]) for key in ("开盘", "最高", "最低", "收盘")),
                     float(row["成交量"]) * volume_scale, float(row["成交额"]))
            for row in frame.to_dict("records")]
