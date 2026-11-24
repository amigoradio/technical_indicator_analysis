"""A-share trading-calendar lookup with an AkShare/BaoStock fallback."""

from datetime import date, time, timedelta
import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _akshare_dates(api: Any, start: date, end: date) -> pd.DataFrame:
    frame = api.tool_trade_date_hist_sina()
    if frame is None or frame.empty or "trade_date" not in frame:
        raise ValueError("AkShare 交易日历为空")
    days = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    days = [day for day in days if pd.notna(day) and start <= day <= end]
    return pd.DataFrame({"calendar_date": days, "is_trading_day": 1})


def _baostock_dates(start: date, end: date) -> pd.DataFrame:
    import baostock as bs

    login = bs.login()
    if getattr(login, "error_code", "0") != "0":
        raise RuntimeError("BaoStock 登录失败")
    try:
        result = bs.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat())
        if getattr(result, "error_code", "0") != "0":
            raise RuntimeError("BaoStock 交易日历查询失败")
        frame = result.get_data()
        if frame is None or frame.empty:
            raise ValueError("BaoStock 交易日历为空")
        frame["calendar_date"] = pd.to_datetime(frame["calendar_date"], errors="coerce").dt.date
        frame["is_trading_day"] = pd.to_numeric(frame["is_trading_day"], errors="coerce").fillna(0).astype(int)
        return frame[["calendar_date", "is_trading_day"]]
    finally:
        bs.logout()


def trading_calendar(
    start: date,
    end: date,
    *,
    akshare_api: Any | None = None,
) -> pd.DataFrame:
    """Return calendar rows including holidays, preferring AkShare and falling back to BaoStock."""
    if start > end:
        raise ValueError("交易日历开始日期不能晚于结束日期")
    try:
        import akshare as ak
        frame = _akshare_dates(akshare_api or ak, start, end)
        logger.info("使用 AkShare 获取交易日历")
        return frame.sort_values("calendar_date", ignore_index=True)
    except Exception as exc:
        logger.warning("AkShare 交易日历不可用（%s），切换 BaoStock", type(exc).__name__)
        return _baostock_dates(start, end).sort_values("calendar_date", ignore_index=True)


def is_trading_day(day: date, *, akshare_api: Any | None = None) -> bool:
    """Check an individual date, including exchange holidays."""
    frame = trading_calendar(day, day, akshare_api=akshare_api)
    return bool(not frame.empty and int(frame.iloc[0]["is_trading_day"]) == 1)


def resolve_target(
    now,
    *,
    scheduled: bool = False,
    close_hour: int = 16,
    akshare_api: Any | None = None,
) -> date | None:
    """Resolve the report session for scheduled or manually triggered Actions."""
    today = now.date()
    calendar = trading_calendar(today - timedelta(days=31), today, akshare_api=akshare_api)
    trading = [row.calendar_date for row in calendar.itertuples()
               if int(row.is_trading_day) == 1 and row.calendar_date <= today]
    if scheduled:
        return today if today in trading else None
    local_time = now.replace(tzinfo=None).time()
    # 16:00 exactly is still treated as intraday. Only times strictly after
    # 16:00 use today's session, matching the manual Actions rule.
    if today in trading and local_time > time(close_hour):
        return today
    previous = [day for day in trading if day < today]
    return previous[-1] if previous else None
