"""Equity risk premium persistence and local ten-year reconciliation."""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .calendar import resolve_target, trading_calendar
from .csv_store import CsvStore, parse_dates

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
DATE = "交易日期"
PE = "中证全指pettm"
BOND = "10年国债收益率"
SPREAD = "股债利差"
PERCENTILE = "10年百分比"
COLUMNS = (DATE, PE, BOND, SPREAD, PERCENTILE)
ERP_CSV = Path(__file__).resolve().parents[1] / "csv_data" / "erp.csv"
INDEX_CODE = "000985"
CSINDEX_PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


@dataclass(frozen=True)
class ErpInfo:
    trade_date: date
    pe: Decimal
    bond_yield: Decimal
    spread: Decimal
    percentile: Decimal


def read_erp_info(today: date, path: Path = ERP_CSV) -> ErpInfo | None:
    """Read the latest usable ERP record on or before the report date."""
    try:
        frame = CsvStore(path, DATE, COLUMNS).read()
    except (OSError, ValueError) as exc:
        logger.warning("读取 ERP CSV 失败（%s）", type(exc).__name__)
        return None
    eligible = frame.loc[frame[DATE] <= pd.Timestamp(today)]
    for day, pe, bond_yield, spread, percentile in eligible.iloc[::-1].itertuples(index=False, name=None):
        try:
            values = tuple(Decimal(str(value)) for value in (pe, bond_yield, spread, percentile))
        except InvalidOperation:
            continue
        if all(value.is_finite() for value in values) and values[0] > 0 and 0 <= values[-1] <= 100:
            return ErpInfo(day.date(), *values)
    return None


def years_before(day: date, years: int) -> date:
    return (pd.Timestamp(day) - pd.DateOffset(years=years)).date()


def _numbers(frame: pd.DataFrame, column: int | str) -> pd.Series:
    values = frame.iloc[:, column] if isinstance(column, int) else frame[column]
    return pd.to_numeric(values, errors="coerce")


class ErpSource:
    def __init__(self, api, request_interval: float = 3.0):
        self.api = api
        self.request_interval = max(0.0, request_interval)

    def _pause(self) -> None:
        if self.request_interval:
            sleep(self.request_interval)

    def historical_pe(self, start: date, end: date) -> pd.DataFrame:
        self._pause()
        response = requests.get(
            CSINDEX_PERF_URL,
            params={"indexCode": INDEX_CODE, "startDate": start.strftime("%Y%m%d"),
                    "endDate": end.strftime("%Y%m%d")},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ValueError("中证官网行情接口返回失败")
        frame = pd.DataFrame(payload.get("data"))
        if frame.empty:
            return pd.DataFrame(columns=[DATE, PE])
        if not {"indexCode", "tradeDate", "peg"}.issubset(frame.columns):
            raise ValueError("中证官网行情接口缺少指数代码、日期或滚动市盈率字段")
        frame = frame.loc[frame["indexCode"].astype(str).str.zfill(6) == INDEX_CODE]
        return self._pe_rows(frame, "tradeDate", "peg", start, end)

    @staticmethod
    def _pe_rows(frame: pd.DataFrame, date_column: str, pe_column: str,
                 start: date, end: date) -> pd.DataFrame:
        dates = parse_dates(frame[date_column])
        pe = pd.to_numeric(frame[pe_column], errors="coerce")
        return pd.DataFrame({DATE: dates.dt.date, PE: pe}).loc[
            dates.between(pd.Timestamp(start), pd.Timestamp(end)) & pe.gt(0) & pe.lt(float("inf"))
        ]

    def recent_pe(self, start: date, end: date) -> pd.DataFrame:
        try:
            result = self.historical_pe(start, end)
        except (requests.RequestException, ValueError) as exc:
            logger.warning("中证官网近期估值请求失败（%s），切换备用接口", type(exc).__name__)
        else:
            if not result.empty:
                return result
            logger.warning("中证官网未返回区间内的有效估值，切换备用接口")
        self._pause()
        frame = self.api.stock_zh_index_value_csindex(symbol=INDEX_CODE)
        if frame is None or frame.empty:
            return pd.DataFrame(columns=[DATE, PE])
        return self._pe_rows(frame, "日期", "市盈率2", start, end)

    def bond(self, start: date) -> pd.DataFrame:
        self._pause()
        frame = self.api.bond_zh_us_rate(start_date=start.strftime("%Y%m%d"))
        if frame is None or frame.empty:
            return pd.DataFrame(columns=[DATE, BOND])
        result = pd.DataFrame({DATE: parse_dates(frame.iloc[:, 0]), BOND: _numbers(frame, 3)})
        result[DATE] = result[DATE].dt.date
        return result.dropna(subset=[DATE, BOND])


def _calculate(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy().sort_values(DATE).drop_duplicates(subset=[DATE], keep="last")
    frame[SPREAD] = 100 / frame[PE] - frame[BOND]
    dates = pd.DatetimeIndex(frame[DATE])
    values = frame[SPREAD].to_numpy()
    starts = dates.searchsorted(dates - pd.DateOffset(years=10))
    frame[PERCENTILE] = [
        100 * (values[start:position + 1] <= values[position]).sum() /
        len(values[start:position + 1])
        for position, start in enumerate(starts)
    ]
    return frame.loc[:, list(COLUMNS)]


def _merge(pe: pd.DataFrame, bond: pd.DataFrame) -> pd.DataFrame:
    pe = pe.sort_values(DATE).drop_duplicates(subset=[DATE], keep="last")
    bond = bond.sort_values(DATE).drop_duplicates(subset=[DATE], keep="last")
    pe = pe.assign(**{DATE: pd.to_datetime(pe[DATE])})
    bond = bond.assign(**{DATE: pd.to_datetime(bond[DATE])})
    merged = pd.merge_asof(pe, bond, on=DATE, direction="backward",
                           tolerance=pd.Timedelta(days=7)).dropna(subset=[PE, BOND])
    merged[DATE] = merged[DATE].dt.date
    return merged


def sync_erp(source: ErpSource, now: datetime, *, path: Path = ERP_CSV,
             repair: bool = False, scheduled: bool = False) -> None:
    if now.tzinfo is None:
        raise ValueError("运行时间必须带时区")
    now = now.astimezone(SHANGHAI)
    target = resolve_target(now, scheduled=scheduled, akshare_api=source.api)
    if target is None:
        logger.info("%s 非交易日或尚未收盘，跳过 ERP 更新", now.date())
        return
    start = years_before(target, 10)
    store = CsvStore(path, DATE, COLUMNS, float_digits=2)
    existing = None if repair else store.read()
    if existing is not None and existing.empty:
        raise ValueError("erp.csv 没有历史数据，请先运行 python -m stock_report.erp repair")
    if repair:
        pe = source.historical_pe(start, target)
    else:
        pe = source.recent_pe(target - timedelta(days=30), target)
    if pe.empty:
        raise ValueError("区间内没有中证全指（000985）的有效 PE-TTM")
    bond = source.bond(pe[DATE].min() - timedelta(days=7))
    incoming = _merge(pe, bond)
    if incoming.empty:
        raise ValueError("ERP 接口没有返回可合并的数据")
    requested = sorted(set(incoming[DATE]))
    if repair:
        calendar = trading_calendar(start, requested[-1], akshare_api=source.api)
        sessions = set(calendar.loc[calendar["is_trading_day"] == 1, "calendar_date"])
        missing = sessions.difference(requested)
        if not sessions or missing or (target - requested[-1]).days > 7:
            raise ValueError(f"ERP 历史覆盖不足，交易日缺失 {len(missing)} 个，最新 {requested[-1]}")
        # The official feed can include a valuation on an exchange holiday.
        incoming = incoming.loc[incoming[DATE].isin(sessions)]
        requested = sorted(sessions)
    result = store.sync(incoming, start=start, end=target, expected_dates=requested,
                        derive=_calculate,
                        replace=repair, existing=existing)
    logger.info("%s 已更新 ERP %d 行，保留 %d 行", path, len(incoming), len(result))


def main() -> int:
    parser = argparse.ArgumentParser(description="股权风险溢价 ERP CSV")
    parser.add_argument("command", choices=("update", "repair"))
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import akshare as ak
    try:
        sync_erp(ErpSource(ak), datetime.now(SHANGHAI),
                 repair=args.command == "repair", scheduled=args.scheduled)
    except Exception:
        logger.exception("ERP 处理失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
