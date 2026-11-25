"""A-share daily advance/decline breadth statistics."""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .calendar import resolve_target, trading_calendar
from .csv_store import CsvStore, parse_dates

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
DATE = "交易日期"
COLUMNS = (DATE, "涨幅大于5%", "涨幅大于9.8%", "跌幅大于5%", "跌幅大于9.8%", "统计股票总数")
CHARGE_CSV = Path(__file__).resolve().parents[1] / "csv_data" / "charge.csv"
PREFIXES = ("60", "00", "300", "688")


@dataclass(frozen=True)
class ChargeInfo:
    rows: tuple[tuple[date, int, int, int, int, int], ...] = ()


def read_charge(path: Path = CHARGE_CSV, *, target: date, limit: int = 10) -> ChargeInfo:
    try:
        frame = CsvStore(path, DATE, COLUMNS, float_digits=0).read()
    except (OSError, ValueError):
        return ChargeInfo()
    rows = []
    for _, row in frame.loc[frame[DATE] <= pd.Timestamp(target)].iterrows():
        try:
            counts = [float(row[column]) for column in COLUMNS[1:]]
        except (TypeError, ValueError):
            continue
        if not all(isfinite(count) and count >= 0 and count.is_integer() for count in counts):
            continue
        up5, up98, down5, down98, total = map(int, counts)
        if up98 <= up5 and down98 <= down5 and up5 + down5 <= total and total > 0:
            rows.append((row[DATE].date(), up5, up98, down5, down98, total))
    return ChargeInfo(tuple(rows[-limit:]) if limit > 0 else ())


def count_snapshot(frame: pd.DataFrame) -> dict[str, int]:
    required = {"代码", "名称", "涨跌幅"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        raise ValueError("A 股快照缺少代码、名称或涨跌幅字段")
    rows = frame.copy()
    codes = rows["代码"].astype(str).str.strip().str.zfill(6)
    names = rows["名称"].astype(str).str.strip().str.lower()
    change = pd.to_numeric(rows["涨跌幅"], errors="coerce")
    valid = (codes.str.startswith(PREFIXES) & change.notna() &
             ~names.str.contains("etf|st|reit|lof", regex=True, na=False))
    change = change.loc[valid]
    return {
        "涨幅大于5%": int((change > 5).sum()),
        "涨幅大于9.8%": int((change > 9.8).sum()),
        "跌幅大于5%": int((change < -5).sum()),
        "跌幅大于9.8%": int((change < -9.8).sum()),
        "统计股票总数": int(change.size),
    }


def sync_charge(frame: pd.DataFrame, day: date, *, path: Path = CHARGE_CSV) -> None:
    store = CsvStore(path, DATE, COLUMNS, float_digits=0)
    existing = store.read(repair=False)
    values = count_snapshot(frame)
    incoming = pd.DataFrame([{DATE: day, **values}])
    start = (pd.Timestamp(day) - pd.DateOffset(years=1)).date()
    store.sync(incoming, start=start, end=day, expected_dates=[day],
               derive=lambda data: data, existing=existing)


def main() -> int:
    parser = argparse.ArgumentParser(description="A 股涨跌幅统计")
    parser.add_argument("command", choices=("update",))
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import akshare as ak
    now = datetime.now(SHANGHAI)
    target = resolve_target(now, scheduled=args.scheduled)
    if target is None:
        logger.info("%s 非交易日或尚未收盘，跳过涨跌幅统计", now.date())
        return 0
    try:
        frame = ak.stock_zh_a_spot_em()
        sync_charge(frame, target)
        logger.info("%s 已写入涨跌幅统计", target)
    except Exception:
        logger.exception("涨跌幅统计失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
