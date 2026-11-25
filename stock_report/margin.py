"""Daily A-share margin balances and local five-year reconciliation."""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import math
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd

from .csv_store import CsvStore, parse_dates
from .calendar import resolve_target, trading_calendar

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
DATE = "信用交易日期"
BALANCE = "融资余额(亿)"
CHANGE = "融资净增量"
PERCENTILES = {3: "融资余额3年百分比", 5: "融资余额5年百分比"}
COLUMNS = (DATE, BALANCE, CHANGE, *PERCENTILES.values())
MARGIN_CSV = Path(__file__).resolve().parents[1] / "csv_data" / "margin.csv"


@dataclass(frozen=True)
class MarginInfo:
    latest_date: date | None = None
    balance: object = None
    change: object = None
    percentile_3y: object = None
    percentile_5y: object = None
    status: str = "无法获取数据"
    recent: tuple[tuple[date, float, float], ...] = ()


def margin_info(today: date, path: Path = MARGIN_CSV) -> MarginInfo:
    """Return the latest stored financing row and freshness/completeness status."""
    try:
        frame = read_margin(path)
    except Exception:
        return MarginInfo()
    if frame.empty:
        return MarginInfo()
    latest = frame.iloc[-1]
    latest_date = latest[DATE].date()
    statuses = []
    if (today - latest_date).days > 10:
        statuses.append("没有最新数据")
    try:
        expected_start = years_before(today, 5)
        expected = trading_calendar(expected_start, latest_date)
        expected_dates = {
            row.calendar_date for row in expected.itertuples()
            if int(row.is_trading_day) == 1
        }
        actual_dates = set(frame[DATE].dt.date)
        if expected_dates.difference(actual_dates):
            statuses.append("数据不完整")
    except Exception:
        # A calendar outage must not hide an otherwise usable stored row.
        pass
    recent_rows = []
    for _, row in frame.tail(10).iterrows():
        recent_rows.append((row[DATE].date(), row.get(BALANCE), row.get(CHANGE)))
    return MarginInfo(
        latest_date=latest_date,
        balance=latest.get(BALANCE),
        change=latest.get(CHANGE),
        percentile_3y=latest.get(PERCENTILES[3]),
        percentile_5y=latest.get(PERCENTILES[5]),
        status="；".join(statuses),
        recent=tuple(recent_rows),
    )


class PublicationPending(RuntimeError):
    """A requested session's exchange summary is not yet available."""


def read_margin(path: Path = MARGIN_CSV) -> pd.DataFrame:
    """Read the stored financing history without accessing an exchange."""
    return CsvStore(path, DATE, COLUMNS, float_digits=8).read()


def years_before(day: date, years: int) -> date:
    return (pd.Timestamp(day) - pd.DateOffset(years=years)).date()


def _positive_balance(value: object) -> float:
    try:
        amount = float(str(value).replace(",", ""))
    except ValueError:
        raise ValueError("融资余额不是有效数值") from None
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("融资余额必须为有限正数")
    return amount


class MarginSource:
    def __init__(self, api):
        self.api = api

    def sessions(self, today: date) -> list[date]:
        frame = trading_calendar(today - timedelta(days=2200), today, akshare_api=self.api)
        return [row.calendar_date for row in frame.itertuples()
                if int(row.is_trading_day) == 1]

    def shanghai(self, start: date, end: date) -> dict[date, float]:
        try:
            frame = self.api.stock_margin_sse(start_date=start.strftime("%Y%m%d"),
                                              end_date=end.strftime("%Y%m%d"))
        except ValueError as exc:
            # Akshare 1.20.39 assigns column names even to an empty SSE result.
            if "Expected axis has 0 elements" in str(exc):
                return {}
            raise
        if frame.empty:
            return {}
        days = parse_dates(frame[DATE])
        if days.isna().any():
            raise ValueError("上交所返回无效交易日期")
        return {day: _positive_balance(amount) / 1e8
                for day, amount in zip(days.dt.date, frame["融资余额"])
                if start <= day <= end}

    def shenzhen(self, day: date) -> float:
        try:
            frame = self.api.stock_margin_szse(date=day.strftime("%Y%m%d"))
        except ValueError as exc:
            # The SZSE wrapper also assigns columns to unpublished empty data.
            if "Expected axis has 0 elements" in str(exc):
                raise PublicationPending(f"{day} 深交所融资余额尚未发布") from None
            raise
        finally:
            sleep(1)
        if frame.empty:
            raise PublicationPending(f"{day} 深交所融资余额尚未发布")
        if len(frame) != 1:
            raise ValueError(f"{day} 深交所未返回唯一汇总行")
        return _positive_balance(frame["融资余额"].iloc[0])


def _derive(frame: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    frame = frame.copy()
    frame[BALANCE] = frame[BALANCE].map(_positive_balance)
    dates = pd.DatetimeIndex(frame[DATE])
    calendar = pd.DatetimeIndex(sessions)
    balances = pd.Series(frame[BALANCE].to_numpy(), index=dates)
    frame[CHANGE] = (balances.reindex(calendar).diff().reindex(dates)
                     .fillna(0).to_numpy())
    values = balances.to_numpy()
    for years, column in PERCENTILES.items():
        starts = dates.searchsorted(dates - pd.DateOffset(years=years))
        ranks = []
        for position, start in enumerate(starts):
            window = values[start:position + 1]
            ranks.append(100 * (window <= values[position]).sum() / len(window))
        frame[column] = pd.Series(ranks, index=frame.index)
    return frame


def sync_margin(
    source: MarginSource,
    now: datetime,
    *,
    path: Path = MARGIN_CSV,
    repair: bool = False,
    scheduled: bool = False,
) -> None:
    """Update the latest three sessions, or reconcile five years for local repair."""
    if now.tzinfo is None:
        raise ValueError("运行时间必须带时区")
    now = now.astimezone(SHANGHAI)
    today = now.date()
    start = years_before(today, 5)
    sessions = source.sessions(today)
    target = resolve_target(now, scheduled=scheduled, akshare_api=source.api)
    if target is None:
        logger.info("%s A 股休市或尚未到收盘时间，跳过更新", today)
        return
    expected = [day for day in sessions if start <= day <= target]
    if not expected:
        raise ValueError("交易日历中找不到目标日期")
    target = expected[-1]
    store = CsvStore(path, DATE, COLUMNS, float_digits=8)
    existing = store.read(repair=repair)
    if not repair and existing.empty:
        raise ValueError("margin.csv 没有历史数据，请先在本地运行 python -m stock_report.margin repair")
    requested = expected if repair else expected[-3:]
    sse = source.shanghai(start if repair else requested[0], target)
    pending = None
    if repair:
        missing = set(requested).difference(sse)
        missing_history = missing.difference({today})
        if missing_history:
            raise ValueError(f"上交所缺少历史交易日：{sorted(missing_history)}")
        if missing:
            pending = PublicationPending(f"{target} 上交所融资余额尚未发布")
    rows = []
    for day in requested:
        if day not in sse:
            if not repair:
                logger.info("%s 上交所融资余额尚未发布，跳过该交易日", day)
            continue
        try:
            szse = source.shenzhen(day)
        except PublicationPending as exc:
            if not repair:
                logger.info("%s，跳过该交易日", exc)
                continue
            if day != target:
                raise
            pending = exc
            continue
        rows.append({DATE: day, BALANCE: sse[day] + szse})
        if len(rows) % 100 == 0:
            logger.info("已核对 %d/%d 个交易日", len(rows), len(requested))

    if rows:
        if pending and target in existing[DATE].dt.date.values:
            raise pending
        confirmed = [row[DATE] for row in rows]
        result = store.sync(pd.DataFrame(rows), start=start, end=target,
                            expected_dates=confirmed, derive=lambda frame: _derive(frame, sessions),
                            replace=repair, existing=existing)
        logger.info("%s 已核对 %d 个交易日，保留 %d 行，最新日期 %s",
                    path, len(rows), len(result), result[DATE].iloc[-1].date())
    elif not repair:
        logger.info("最近 %d 个交易日均无完整沪深融资数据，保留现有 CSV", len(requested))
    if pending:
        raise pending


def main() -> int:
    parser = argparse.ArgumentParser(description="沪深融资余额 CSV")
    parser.add_argument("command", choices=("update", "repair"), help="增量更新或本地全量校验修复")
    parser.add_argument("--scheduled", action="store_true", help="按交易日历跳过休市日")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import akshare as ak

    try:
        sync_margin(MarginSource(ak), datetime.now(SHANGHAI),
                    repair=args.command == "repair", scheduled=args.scheduled)
    except PublicationPending as exc:
        logger.warning("%s", exc)
        return 2
    except Exception:
        logger.exception("融资余额处理失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
