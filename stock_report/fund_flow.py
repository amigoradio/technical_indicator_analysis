"""Optional main-fund flows for the daily close report."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http.client import RemoteDisconnected
import json
import logging
from math import isfinite
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .calendar import trading_calendar
from .data import worker_pipe
from .models import Stock
from .providers import longbridge

logger = logging.getLogger(__name__)
NET = "主力净流入-净额"
SHANGHAI = ZoneInfo("Asia/Shanghai")
FLOW_WINDOW = 5
STOCK_FALLBACK_INTERVAL = 300


class _InterfaceBlocked(Exception):
    """Stop querying this endpoint for the rest of the report."""


@dataclass(frozen=True)
class FundFlow:
    amount: float
    currency: str
    streak: str = ""
    divergence: str = ""


def _date(value) -> date | None:
    stamp = pd.to_datetime(str(value).strip(), errors="coerce")
    if pd.isna(stamp):
        return None
    return (stamp.tz_convert(SHANGHAI) if stamp.tzinfo else stamp).date()


def _amount(value) -> float | None:
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if isfinite(amount) else None


def _akshare_flow_frame(fetch, row_field: str, **kwargs) -> pd.DataFrame:
    from akshare.utils import http

    # Scope the transport guard to this call in the isolated worker. ETF
    # pagination otherwise retries HTTP 429 before returning control.
    get, request_with_retry = http.get, http.request_with_retry

    def check(response):
        response.raise_for_status()
        data = response.json().get("data")
        if data is None or not data.get(row_field):
            raise _InterfaceBlocked("empty data")
        return response

    def guarded_get(*args, **options):
        return check(get(*args, **options))

    def guarded_page(*args, **options):
        options["max_retries"] = 1
        return check(request_with_retry(*args, **options))

    http.get, http.request_with_retry = guarded_get, guarded_page
    try:
        frame = fetch(**kwargs)
    finally:
        http.get, http.request_with_retry = get, request_with_retry
    if frame is None or frame.empty:
        raise _InterfaceBlocked("empty data")
    return frame


def _stock_history(stock: Stock) -> dict[date, float | None]:
    import akshare as ak

    frame = _akshare_flow_frame(ak.stock_individual_fund_flow, "klines",
                                stock=stock.code, market=stock.exchange.lower())
    return {day: _amount(amount) for raw_date, amount in zip(frame["日期"], frame[NET])
            if (day := _date(raw_date)) is not None}


def _stock_flow_fallback(stock: Stock, target: date, timeout: float) -> float | None:
    # AkShare's stock_fund_flow_main_em selects an entire market group.
    # Eastmoney's ulist endpoint accepts the individual stock's secid.
    market_id = 1 if stock.exchange == "SH" else 0
    response = requests.get(
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        params={
            "secids": f"{market_id}.{stock.code}",
            "fields": "f12,f13,f62,f124,f225",
            "fltt": "2",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    rows = (response.json().get("data") or {}).get("diff") or []
    if not rows:
        raise _InterfaceBlocked("empty data")
    if isinstance(rows, dict):
        rows = rows.values()
    for row in rows:
        if str(row.get("f12", "")).zfill(6) != stock.code or str(row.get("f13")) != str(market_id):
            continue
        timestamp = _amount(row.get("f124"))
        if timestamp is None or datetime.fromtimestamp(timestamp, SHANGHAI).date() != target:
            continue
        amount = _amount(row.get("f225"))
        return amount if amount is not None else _amount(row.get("f62"))
    return None


def _etf_snapshot(codes: set[str], target: date) -> dict[str, float | None]:
    import akshare as ak

    frame = _akshare_flow_frame(ak.fund_etf_spot_em, "diff")
    selected = frame.loc[frame["代码"].astype(str).str.strip().str.zfill(6).isin(codes)]
    return {str(code).strip().zfill(6): _amount(amount)
            for code, raw_date, amount in zip(selected["代码"], selected["数据日期"], selected[NET])
            if _date(raw_date) == target}


def _hk_snapshot(stock: Stock, target: date) -> float | None:
    quote = longbridge.quote_request("capital_distribution", stock.longbridge_symbol)
    if quote is None:
        raise _InterfaceBlocked("empty data")
    if _date(quote.timestamp) != target:
        return None
    # Longbridge groups orders into large, medium and small.
    capital_in = _amount(quote.capital_in.large)
    capital_out = _amount(quote.capital_out.large)
    return capital_in - capital_out if capital_in is not None and capital_out is not None else None


def _sessions(market: str, target: date) -> list[date]:
    start = target - timedelta(days=21)
    if market == "CN":
        frame = trading_calendar(start, target)
        dates = frame.loc[frame["is_trading_day"] == 1, "calendar_date"]
    else:
        from longbridge.openapi import Market

        response = longbridge.quote_request("trading_days", Market.HK, start, target)
        dates = response.trading_days + response.half_trading_days
    return sorted({day for day in dates if day <= target})[-FLOW_WINDOW:]


def _blocked_reason(exc: BaseException) -> str | None:
    pending = [exc]
    seen = set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        if isinstance(error, _InterfaceBlocked):
            return str(error)
        if isinstance(error, RemoteDisconnected) or type(error).__name__ == "Push2RateLimitError":
            return type(error).__name__
        response = getattr(error, "response", None)
        if getattr(error, "status_code", None) == 429 or getattr(response, "status_code", None) == 429:
            return "HTTP 429"
        pending.extend(item for item in (*error.args, error.__cause__, error.__context__)
                       if isinstance(item, BaseException))
    return None


def _worker(connection, operation, args) -> None:
    try:
        connection.send(("ok", operation(*args)))
    except Exception as exc:
        reason = _blocked_reason(exc)
        connection.send(("blocked", reason) if reason else ("error", type(exc).__name__))
    finally:
        connection.close()


def _request(operation, timeout: float, *args):
    try:
        with worker_pipe(_worker, operation, args) as reader:
            if not reader.poll(timeout):
                raise TimeoutError
            status, result = reader.recv()
    except Exception as exc:
        status, result = "error", type(exc).__name__
    if status == "ok":
        return result
    if status == "blocked":
        logger.warning("本次任务停止请求 %s (%s)", operation.__name__, result)
        raise _InterfaceBlocked(result)
    logger.warning("资金情况查询失败 %s (%s)", operation.__name__, result)
    return None


def _read_history(path: Path | None, codes: set[str], earliest: date) -> dict[str, dict[date, float | None]]:
    history = {code: {} for code in codes}
    if path is None:
        return history
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return history
    except (OSError, ValueError) as exc:
        logger.warning("资金快照读取失败 (%s)", type(exc).__name__)
        return history
    if isinstance(data, dict):
        for code, rows in history.items():
            saved = data.get(code)
            if isinstance(saved, dict):
                for key, amount in saved.items():
                    day = _date(key)
                    if day is not None and day >= earliest:
                        rows[day] = _amount(amount)
    return history


def _save_history(path: Path, history: dict[str, dict[date, float | None]]) -> None:
    data = {code: {day.isoformat(): amount for day, amount in rows.items()}
            for code, rows in history.items() if rows}
    if not data:
        return
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as file:
            temporary = Path(file.name)
            json.dump(data, file, allow_nan=False)
        temporary.replace(path)
    except (OSError, ValueError) as exc:
        logger.warning("资金快照保存失败 (%s)", type(exc).__name__)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _summarize(known: dict[date, float | None], target: date,
               sessions: list[date], currency: str,
               closes: Mapping[date, float] | None = None) -> FundFlow | None:
    amount = known.get(target)
    if not amount:
        return None
    if len(sessions) != FLOW_WINDOW or sessions[-1] != target:
        return FundFlow(amount, currency)

    values = [known.get(day) for day in sessions]
    all_in = all(value is not None and value > 0 for value in values)
    all_out = all(value is not None and value < 0 for value in values)
    streak = "连续流入" if all_in else "连续流出" if all_out else ""
    divergence = ""
    if streak and closes and all(day in closes for day in sessions):
        first, last = closes[sessions[0]], closes[sessions[-1]]
        if all_out and last > first:
            divergence = "主力出货"
        elif all_in and last < first:
            divergence = "主力抄底"
    return FundFlow(amount, currency, streak, divergence)


def collect_fund_flows(
    stocks: Sequence[Stock], target: date, today: date, timeout: float, *,
    closes_by_code: Mapping[str, Mapping[date, float]] | None = None,
    scheduled: bool = False,
) -> dict[str, FundFlow]:
    """Fetch historical stock flows and accumulate dated ETF/HK snapshots."""
    hk = [stock for stock in stocks if stock.market == "HK"] if longbridge.configured() else []
    etfs = {stock.code for stock in stocks if stock.is_etf}
    mainland = [stock for stock in stocks if stock.market == "CN" and not stock.is_etf]
    cache_name = os.getenv("FUND_FLOW_HISTORY_FILE")
    path = Path(cache_name) if cache_name else None
    history = _read_history(path, etfs | {stock.code for stock in hk}, today - timedelta(days=30))
    if target == today:
        if etfs:
            try:
                snapshot = _request(_etf_snapshot, timeout, etfs, target)
            except _InterfaceBlocked:
                snapshot = {}
            if snapshot is not None:
                for code in etfs:
                    history[code][target] = snapshot.get(code)
        for stock in hk:
            history[stock.code].pop(target, None)
        for stock in hk:
            try:
                history[stock.code][target] = _request(_hk_snapshot, timeout, stock, target)
            except _InterfaceBlocked:
                break
        if path is not None and history:
            _save_history(path, history)

    for stock in mainland:
        try:
            stock_history = _request(_stock_history, timeout, stock)
        except _InterfaceBlocked:
            break
        if stock_history:
            history[stock.code] = stock_history

    if scheduled and target == today:
        missing = [stock for stock in mainland if history.get(stock.code, {}).get(target) is None]
        for index, stock in enumerate(missing):
            if index:
                logger.info("等待 5 分钟后查询下一只股票的备用资金数据")
                sleep(STOCK_FALLBACK_INTERVAL)
            try:
                amount = _request(_stock_flow_fallback, timeout, stock, target, timeout)
            except _InterfaceBlocked:
                break
            if amount is not None:
                history.setdefault(stock.code, {})[target] = amount

    flows = {}
    for market, currency in (("CN", "CNY"), ("HK", "HKD")):
        matches = [stock for stock in stocks if stock.market == market and history.get(stock.code, {}).get(target)]
        if not matches:
            continue
        try:
            sessions = _request(_sessions, timeout, market, target) or []
        except _InterfaceBlocked:
            sessions = []
        for stock in matches:
            closes = closes_by_code.get(stock.code) if closes_by_code else None
            flow = _summarize(history[stock.code], target, sessions, currency, closes)
            if flow:
                flows[stock.code] = flow
    return flows
