"""Backfill or update index valuations."""

import argparse
from datetime import date, datetime, timedelta
import logging
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .calendar import resolve_target
from .csv_store import parse_dates
from .erp import CSINDEX_PERF_URL, years_before

DATA_DIR = Path(__file__).resolve().parents[1] / "csv_data"
INDEX_FILE = DATA_DIR / "market_index.csv"
VALUATION_FILE = DATA_DIR / "pepb.csv"
DAY, CODE, NAME = "交易日期", "指数代码", "简称"
DIVIDEND, PE, PB = "股息率", "pettm", "pb"
METRICS = [DIVIDEND, PE, PB]
PERCENTILES = {PE: "pe百分比", PB: "pb百分比"}
KEY = [CODE, DAY]
COLUMNS = [DAY, CODE, NAME, *METRICS, *PERCENTILES.values()]
CNI_LIST_URL = "https://www.cnindex.com.cn/index/indexList"
CNI_HISTORY_URL = "https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat"
logger = logging.getLogger(__name__)


class IndexSource:
    def __init__(self, api, interval: float) -> None:
        self.api = api
        self.interval = interval
        self.session = requests.Session()
        self.cni_list = None

    def json(self, url: str, **params):
        sleep(self.interval)
        response = self.session.get(url, params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def csindex(self, code: str, start: date, end: date) -> pd.DataFrame:
        datasets = []
        history_api = getattr(self.api, "index_hist_csindex", None) or self.api.stock_zh_index_hist_csindex
        params = {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
        try:
            sleep(self.interval)
            frame = history_api(symbol=code, **params).rename(columns={"日期": DAY, "滚动市盈率": PE})
            datasets.append(frame[[DAY, PE]])
        except (ValueError, KeyError, requests.RequestException) as exc:
            logger.warning("%s AkShare 中证历史获取失败，尝试读取同一官方接口的字段：%s", code, exc)
            try:
                payload = self.json(CSINDEX_PERF_URL, indexCode=code,
                                    startDate=params["start_date"], endDate=params["end_date"])
                if payload.get("success") is not True:
                    raise ValueError(payload.get("msg", "中证历史接口返回失败"))
                frame = pd.DataFrame(payload.get("data"))
                if not frame.empty:
                    frame = frame.loc[frame["indexCode"].astype(str).str.zfill(6).eq(code)]
                    datasets.append(frame.rename(columns={"tradeDate": DAY, "peg": PE})[[DAY, PE]])
            except (ValueError, KeyError, requests.RequestException) as fallback_error:
                logger.warning("%s 中证 PE 历史获取失败：%s", code, fallback_error)

        try:
            sleep(self.interval)
            value_api = getattr(self.api, "index_value_csindex", None) or self.api.stock_zh_index_value_csindex
            frame = value_api(symbol=code)
            if not frame.empty:
                # The indicator file's two P/E fields use different share
                # weights. They are not substitutes for the historical peg field.
                datasets.append(frame.rename(columns={"日期": DAY, "股息率2": DIVIDEND})
                                .reindex(columns=[DAY, DIVIDEND]))
        except Exception as exc:
            logger.warning("%s 中证股息率获取失败：%s", code, exc)

        return self.combine(datasets)

    def cni(self, code: str, start: date, end: date) -> pd.DataFrame:
        if self.cni_list is None:
            payload = self.json(CNI_LIST_URL, channelCode=-1, rows=2000, pageNum=1)
            self.cni_list = {str(row["indexcode"]).zfill(6): row for row in payload["data"]["rows"]}
        snapshot = self.cni_list.get(code)
        if not snapshot:
            return self.combine([])
        # CNIndex publishes PE/PB in its undated snapshot list. Associate it
        # with the latest official quote only when the closing values match.
        payload = self.json(CNI_HISTORY_URL, indexCode=code, startDate=max(start, end - timedelta(days=19)).isoformat(),
                            endDate=end.isoformat(), frequency="day")
        data = payload["data"]
        quotes = pd.DataFrame(data["data"], columns=data["item"])
        quotes["timestamp"] = parse_dates(quotes["timestamp"])
        quotes = quotes.dropna(subset=["timestamp"]).sort_values("timestamp")
        if quotes.empty:
            return self.combine([])
        latest = quotes.iloc[-1]
        try:
            matches = math.isclose(float(latest["close"]), float(snapshot["closeingPoint"]),
                                   rel_tol=0, abs_tol=0.0001)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            logger.warning("%s 国证估值快照与最近收盘点位不一致，无法确认日期", code)
            return self.combine([])
        return pd.DataFrame([{DAY: latest["timestamp"], PE: snapshot.get("peDynamic"),
                              PB: snapshot.get("pb"), DIVIDEND: pd.NA}])

    @staticmethod
    def combine(datasets: list[pd.DataFrame]) -> pd.DataFrame:
        if not datasets:
            return pd.DataFrame(columns=[DAY, *METRICS])
        for frame in datasets:
            frame[DAY] = parse_dates(frame[DAY])
        return (pd.concat(datasets, ignore_index=True).dropna(subset=[DAY])
                .groupby(DAY, as_index=False).last().reindex(columns=[DAY, *METRICS]))


def _read(path: Path, columns: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name} 缺少字段：{', '.join(sorted(missing))}")
    frame[CODE] = frame[CODE].fillna("").str.strip().str.upper().str.zfill(6)
    if not frame[CODE].str.fullmatch(r"[A-Z0-9]{6}").all() or frame[CODE].eq("000000").any():
        raise ValueError(f"{path.name} 含无效指数代码")
    return frame[columns]


def _metric_values(frame: pd.DataFrame, metric: str) -> pd.Series:
    values = pd.to_numeric(frame[metric], errors="coerce")
    values = values.where(values.abs().lt(float("inf")))
    return values.where(values.ne(0)) if metric == PB else values


def _history_ready(frame: pd.DataFrame, values: pd.Series) -> pd.Series:
    first_valid = frame[DAY].where(values.notna()).groupby(frame[CODE]).transform("min")
    return frame[DAY].ge(first_valid + pd.DateOffset(years=1))


def merge(existing: pd.DataFrame, incoming: list[pd.DataFrame], start: date, end: date) -> pd.DataFrame:
    frames = ([existing] if not existing.empty else []) + incoming
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.concat(frames, ignore_index=True)
    frame[DAY] = parse_dates(frame[DAY])
    frame = (frame.loc[frame[DAY].between(pd.Timestamp(start), pd.Timestamp(end))]
             .drop_duplicates(subset=KEY, keep="first").sort_values(KEY, ignore_index=True))
    if frame.empty:
        return frame.reindex(columns=COLUMNS)
    for metric, percentile in PERCENTILES.items():
        values = _metric_values(frame, metric)
        # Each row uses the available observations up to its own date.
        groups = values.groupby(frame[CODE])
        ranks = groups.expanding().rank(method="max").droplevel(0)
        counts = values.notna().groupby(frame[CODE]).cumsum()
        frame[percentile] = (ranks / counts * 100).where(_history_ready(frame, values)).round(2)
    return frame.reindex(columns=COLUMNS)


def read_weekly_rows(target: date, *, index_path: Path = INDEX_FILE,
                     valuation_path: Path = VALUATION_FILE) -> list[tuple[str, ...]]:
    indexes = _read(index_path, [CODE, NAME]).drop_duplicates(CODE)
    history = _read(valuation_path, COLUMNS)
    history[DAY] = parse_dates(history[DAY])
    history = history.loc[history[DAY].between(pd.Timestamp(years_before(target, 5)), pd.Timestamp(target))].copy()
    history = history.drop_duplicates(KEY).sort_values(DAY)
    for metric, percentile in PERCENTILES.items():
        values = _metric_values(history, metric)
        history[percentile] = history[percentile].where(_history_ready(history, values) & values.notna())
    latest = history.drop_duplicates(CODE, keep="last").set_index(CODE)
    table = indexes.join(latest[[*METRICS, *PERCENTILES.values()]], on=CODE)

    def display(value, default: str, suffix: str = "") -> str:
        value = pd.to_numeric(value, errors="coerce")
        if pd.isna(value) or not math.isfinite(value) or (default == "0" and value == 0):
            return default
        return f"{value:.2f}{suffix}"

    return [
        (name if pd.notna(name) else code, display(dividend, "0", "%"), display(pe, "-"),
         display(pb, "0"), display(pe_rank, "-", "%"), display(pb_rank, "-", "%"))
        for code, name, dividend, pe, pb, pe_rank, pb_rank in table.itertuples(index=False, name=None)
    ]


def _save(path: Path, frame: pd.DataFrame) -> None:
    content = frame.to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n").encode("utf-8-sig")
    if path.exists() and path.read_bytes() == content:
        return
    with NamedTemporaryFile(dir=path.parent, delete=False) as output:
        staged = Path(output.name)
        try:
            output.write(content)
            output.close()
            staged.replace(path)
        finally:
            output.close()
            staged.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="补充指数估值数据")
    parser.add_argument("command", nargs="?", choices=("backfill", "update"), default="backfill")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--market-index", type=Path, default=INDEX_FILE)
    parser.add_argument("--output", type=Path, default=VALUATION_FILE)
    parser.add_argument("--interval", type=float, default=3, help="每次请求前等待的秒数")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 0:
        parser.error("--interval 必须为非负有限数")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import akshare as ak

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date()
    end = resolve_target(now, scheduled=args.scheduled, akshare_api=ak) if args.command == "update" else today
    if end is None:
        logger.info("%s 无目标交易日，跳过指数估值更新", today)
        return 0
    retention_start = years_before(today, 5)
    start = end - timedelta(days=19) if args.command == "update" else retention_start

    indexes = _read(args.market_index, [CODE, NAME, "市场"]).drop_duplicates(CODE)
    existing = _read(args.output, COLUMNS) if args.output.exists() else pd.DataFrame(columns=COLUMNS)
    source = IndexSource(ak, args.interval)
    incoming = []
    incomplete = 0
    try:
        for code, name, market in indexes.itertuples(index=False, name=None):
            try:
                market = str(market).strip()
                if market not in {"1", "2", "3"}:
                    raise ValueError(f"未知市场 {market}")
                fetch = source.cni if market == "3" else source.csindex
                frame = fetch(code, start, end)
                frame[DAY] = parse_dates(frame[DAY])
                for metric in METRICS:
                    values = pd.to_numeric(frame[metric], errors="coerce")
                    frame[metric] = values.where(values.abs().lt(float("inf")))
                frame = frame.loc[frame[DAY].between(pd.Timestamp(start), pd.Timestamp(end))]
                frame = frame.dropna(subset=METRICS, how="all")
                unavailable = [metric for metric in METRICS if not frame[metric].notna().any()]
                if unavailable:
                    incomplete += 1
                    logger.warning("%s %s 无可用字段 %s", code, name, " / ".join(unavailable))
                if not frame.empty:
                    frame = frame.fillna({DIVIDEND: 0, PB: 0})
                    incoming.append(frame.assign(**{CODE: code, NAME: name}))
                logger.info("%s %s 获取 %s 个日期", code, name, len(frame))
            except Exception as exc:
                incomplete += 1
                logger.warning("%s %s 获取失败：%s", code, name, exc)
    finally:
        source.session.close()
    if incoming:
        frame = merge(existing, incoming, retention_start, today)
        _save(args.output, frame)
        logger.info("已保存 %s 行至 %s", len(frame), args.output)
    if incomplete:
        logger.warning("%s 个指数有指标缺失或获取失败", incomplete)
    return 0 if incoming else 1


if __name__ == "__main__":
    raise SystemExit(main())
