"""Provider routing, bounded requests, retries and validation."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from importlib import import_module
import logging
import multiprocessing as mp
import os
import random
import time

from .config import Settings
from .models import DailyBar, Stock, validate_bars
from .providers.longbridge import configured

logger = logging.getLogger(__name__)

_longbridge_cooldown_until = 0.0


def _longbridge_cooldown_seconds() -> float:
    raw = os.getenv("LONGBRIDGE_CONNECTION_COOLDOWN_SECONDS", "15").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("LONGBRIDGE_CONNECTION_COOLDOWN_SECONDS 无效，使用默认 15 秒")
        return 15.0


def _is_longbridge_connection_closed(exc: Exception) -> bool:
    text = str(exc).lower()
    kind = type(exc).__name__.lower()
    markers = ("connectionclosed", "connection_closed", "websocket", "brokenpipe",
               "eoferror", "connectionreset", "connectionaborted", "endofstream")
    return any(marker in kind or marker in text for marker in markers)


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
            and (p.name != "Longbridge" or configured())
            and (p.name != "Baostock" or stock.exchange != "BJ")]


def _worker(connection, provider: Provider, stock: Stock, start: date, end: date) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    try:
        module = import_module(provider.module)
        bars = module.fetch(stock, start, end)
        connection.send((True, bars))
        # Bars are delivered before optional metadata: a slow name endpoint
        # must never invalidate a successful price request.
        try:
            resolver = getattr(module, "get_name", None)
            name = resolver(stock) if resolver else ""
        except Exception as exc:
            logger.warning("%s 名称查询失败（%s），保留行情", stock.code, type(exc).__name__)
            name = ""
        connection.send((True, name))
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
            if payload == "Push2RateLimitError":
                raise RuntimeError("东方财富 Push2 限流（Push2RateLimitError），将退避重试或切换数据源")
            raise RuntimeError(f"数据源调用失败：{payload}（请检查网络、权限及 Secret 配置）")
        if payload and not any(bar.instrument_name for bar in payload) and reader.poll(5):
            try:
                name_ok, name = reader.recv()
                if name_ok and isinstance(name, str) and name.strip():
                    payload = [replace(bar, instrument_name=name.strip()) for bar in payload]
            except (EOFError, OSError):
                pass
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
        global _longbridge_cooldown_until
        errors = []
        try:
            providers = provider_chain(stock)
            start = target - timedelta(days=self.settings.history_days)
        except Exception as exc:
            logger.exception("%s 数据源路由失败", stock.code)
            return FetchResult(stock, [], error=f"数据源路由失败：{type(exc).__name__}")
        for provider in providers:
            if provider.name == "Longbridge" and time.monotonic() < _longbridge_cooldown_until:
                remaining = _longbridge_cooldown_until - time.monotonic()
                message = f"Longbridge 连接冷却中（剩余约 {remaining:.1f} 秒）"
                logger.warning("%s / %s", stock.code, message)
                errors.append(f"Longbridge: {message}")
                continue
            provider_errors = []
            for attempt in range(1, self.settings.attempts + 1):
                delay = random.uniform(2, 5) if attempt == 1 else min(2 ** (attempt + 2), 30) + random.uniform(0, 2)
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
                    if provider.name == "Longbridge" and _is_longbridge_connection_closed(exc):
                        seconds = _longbridge_cooldown_seconds()
                        _longbridge_cooldown_until = time.monotonic() + seconds
                        logger.warning("Longbridge 连接关闭异常，冷却 %.1f 秒", seconds)
                        break
            errors.extend(provider_errors or [f"{provider.name}: 未知错误"])
            logger.info("%s / %s 已耗尽重试，尝试下一数据源", stock.code, provider.name)
        logger.error("%s 未能获得数据", stock.code)
        return FetchResult(stock, [], error="；".join(errors))
