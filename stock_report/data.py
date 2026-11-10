"""Provider routing, bounded requests, retries and validation."""

from dataclasses import dataclass
from datetime import date, timedelta
from importlib import import_module
import logging
import multiprocessing as mp
import random
import time

from .config import Settings
from .models import DailyBar, Stock, validate_bars
from .providers.longbridge import configured

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    name: str
    module: str
    markets: tuple[str, ...]


PROVIDERS = (
    Provider("Longbridge", "stock_report.providers.longbridge", ("CN", "HK")),
    Provider("Akshare", "stock_report.providers.akshare", ("CN", "HK")),
    Provider("Baostock", "stock_report.providers.baostock", ("CN",)),
)


def provider_chain(stock: Stock) -> list[Provider]:
    return [p for p in PROVIDERS if stock.market in p.markets
            and (p.name != "Longbridge" or configured())]


def _worker(connection, provider: Provider, stock: Stock, start: date, end: date) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    try:
        bars = import_module(provider.module).fetch(stock, start, end)
        connection.send((True, bars))
    except Exception as exc:
        # SDK exceptions may contain credential-bearing URLs; never expose their raw text.
        connection.send((False, type(exc).__name__))
    finally:
        connection.close()


def request(provider: Provider, stock: Stock, start: date, end: date,
            timeout: float) -> list[DailyBar]:
    """Use killable processes: thread timeouts leave blocked SDK calls running."""
    context = mp.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(writer, provider, stock, start, end))
    try:
        process.start()
        writer.close()
        if not reader.poll(timeout):
            raise TimeoutError(f"数据请求超过 {timeout:g} 秒")
        try:
            ok, payload = reader.recv()
        except EOFError:
            raise RuntimeError("数据源进程意外退出") from None
        if not ok:
            raise RuntimeError(f"数据源调用失败：{payload}（请检查网络、权限及 Secret 配置）")
        return payload
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()


@dataclass(frozen=True)
class FetchResult:
    stock: Stock
    bars: list[DailyBar]
    source: str = ""
    error: str = ""


class DataService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def fetch(self, stock: Stock, target: date) -> FetchResult:
        errors = []
        try:
            providers = provider_chain(stock)
            start = target - timedelta(days=self.settings.history_days)
        except Exception as exc:
            logger.exception("%s 数据源路由失败", stock.code)
            return FetchResult(stock, [], error=f"数据源路由失败：{type(exc).__name__}")
        for provider in providers:
            provider_errors = []
            for attempt in range(1, self.settings.attempts + 1):
                delay = random.uniform(2, 5)
                logger.info("%s / %s 请求间隔 %.1f 秒", stock.code, provider.name, delay)
                time.sleep(delay)
                logger.info("%s / %s 抓取 %s 至 %s（第 %d/%d 次）", stock.code,
                            provider.name, start, target, attempt, self.settings.attempts)
                began = time.monotonic()
                try:
                    bars = validate_bars(request(provider, stock, start, target,
                                                 self.settings.request_timeout), target)
                    logger.info("%s / %s 成功，%d 根日 K，耗时 %.1f 秒", stock.code,
                                provider.name, len(bars), time.monotonic() - began)
                    return FetchResult(stock, bars, provider.name)
                except Exception as exc:
                    detail = f"{provider.name}: {exc}"
                    provider_errors.append(detail)
                    logger.warning("%s / %s", stock.code, detail)
            errors.extend(provider_errors or [f"{provider.name}: 未知错误"])
            logger.info("%s / %s 已耗尽重试，尝试下一数据源", stock.code, provider.name)
        logger.error("%s 未能获得数据", stock.code)
        return FetchResult(stock, [], error="；".join(errors))
