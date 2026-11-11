"""Select the most recent completed market session, never a partial daily bar."""

from datetime import date, datetime
from functools import lru_cache


@lru_cache(maxsize=2)
def _calendar(market: str):
    import exchange_calendars as xcals

    return xcals.get_calendar({"CN": "XSHG", "HK": "XHKG"}[market])


def completed_session(now: datetime, market: str) -> date:
    """Calendar close includes HK half days and closing auction completion."""
    if now.tzinfo is None:
        raise ValueError("交易日选择需要带时区的当前时间")
    from zoneinfo import ZoneInfo

    local_date = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    calendar = _calendar(market)
    session = calendar.date_to_session(local_date.isoformat(), direction="previous")
    if calendar.session_close(session).to_pydatetime() > now:
        session = calendar.previous_session(session)
    return session.date()
