"""Dated stock valuations for the daily and weekly reports."""

from dataclasses import dataclass
from datetime import date
import logging
from math import isfinite

import pandas as pd

from .csv_store import parse_dates
from .data import worker_pipe
from .models import Stock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Valuation:
    date: date
    pe_ttm: float | None
    pb: float | None


def _values(frame: pd.DataFrame, date_column: str, column: str, target: date) -> dict[date, float]:
    if frame is None or frame.empty or not {date_column, column}.issubset(frame.columns):
        return {}
    dates = parse_dates(frame[date_column])
    values = pd.to_numeric(frame[column], errors="coerce")
    cutoff = pd.Timestamp(target)
    return {day.date(): float(value) for day, value in zip(dates, values)
            if pd.notna(day) and day <= cutoff and pd.notna(value) and isfinite(value)}


def _fetch(stock: Stock, target: date) -> Valuation | None:
    import akshare as ak

    if stock.market == "CN":
        frame = ak.stock_value_em(symbol=stock.code)
        pe = _values(frame, "数据日期", "PE(TTM)", target)
        pb = _values(frame, "数据日期", "市净率", target)
    else:
        series = []
        for indicator in ("市盈率(TTM)", "市净率"):
            try:
                frame = ak.stock_hk_valuation_baidu(symbol=stock.code[2:], indicator=indicator)
                series.append(_values(frame, "date", "value", target))
            except Exception as exc:
                logger.warning("%s %s 查询失败（%s）", stock.code, indicator, type(exc).__name__)
                series.append({})
        pe, pb = series

    latest = max(pe.keys() | pb.keys(), default=None)
    return Valuation(latest, pe.get(latest), pb.get(latest)) if latest is not None else None


def _worker(connection, stock: Stock, target: date) -> None:
    try:
        connection.send((True, _fetch(stock, target)))
    except Exception as exc:
        connection.send((False, type(exc).__name__))
    finally:
        connection.close()


def read_valuation(stock: Stock, target: date, timeout: float) -> Valuation | None:
    if not stock.supports_fundamentals:
        return None
    try:
        with worker_pipe(_worker, stock, target) as reader:
            if not reader.poll(timeout):
                raise TimeoutError
            ok, value = reader.recv()
        if ok:
            return value
        error = value
    except Exception as exc:
        error = type(exc).__name__
    logger.warning("%s 估值查询失败（%s）", stock.code, error)
    return None
