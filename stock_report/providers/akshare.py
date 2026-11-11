from datetime import date
import logging

from ..models import DailyBar, Stock, validate_bars

logger = logging.getLogger(__name__)


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
        kwargs["symbol"] = stock.code
        try:
            frame = ak.fund_etf_hist_em(**kwargs)
            bars = _to_bars(frame, stock)
            # An empty or stale response must also trigger the alternate API.
            return validate_bars(bars, end)
        except Exception as primary_error:
            logger.warning("ETF %s 东方财富接口不可用（%s），尝试备用接口", stock.code, type(primary_error).__name__)
            # Eastmoney Push2 is frequently rate-limited from GitHub-hosted
            # runners. Try Akshare's Sina ETF endpoint before declaring the
            # provider unavailable; both are still the same provider layer.
            fallback = getattr(ak, "fund_etf_hist_sina", None)
            if fallback is None:
                raise RuntimeError("ETF Eastmoney接口失败且Akshare无Sina备用接口") from primary_error
            try:
                # Sina expects the exchange-prefixed symbol (sh510050/sz159915).
                frame = fallback(symbol=f"{stock.exchange.lower()}{stock.code}")
            except Exception as fallback_error:
                raise RuntimeError("ETF Akshare接口被限流或不可用（Eastmoney/Sina均失败）") from fallback_error
        return _to_bars(frame, stock)
    else:
        api = ak.stock_zh_a_hist
        kwargs["symbol"] = stock.code
    frame = api(**kwargs)
    return _to_bars(frame, stock)


def _to_bars(frame, stock: Stock) -> list[DailyBar]:
    if frame is None or frame.empty:
        return []
    # Eastmoney A-share history reports lots (100 shares); HK history reports shares.
    volume_scale = 1 if stock.market == "HK" else 100
    rows = frame.to_dict("records")
    bars = []
    for row in rows:
        date_value = row.get("日期", row.get("date"))
        amount = row.get("成交额", row.get("amount"))
        bars.append(DailyBar(date.fromisoformat(str(date_value)[:10]),
                             *(float(row.get(key, row.get(key_en)))
                               for key, key_en in (("开盘", "open"), ("最高", "high"),
                                                   ("最低", "low"), ("收盘", "close"))),
                             float(row.get("成交量", row.get("volume", 0))) * volume_scale,
                             float(amount) if amount not in (None, "") else None))
    return bars
