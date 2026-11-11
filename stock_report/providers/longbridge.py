"""Longbridge auth and endpoints follow daily_stock_analysis's conventions."""

import base64
from datetime import date, datetime
import json
import logging
import os
from pathlib import Path

from ..config import value
from ..models import DailyBar, Stock

logger = logging.getLogger(__name__)


def configured() -> bool:
    return any(value(key) for key in ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_OAUTH_CLIENT_ID",
                                      "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"))


def reject_interactive_auth(_url: str) -> None:
    raise RuntimeError("OAuth 缓存失效，请更新 LONGBRIDGE_OAUTH_TOKEN_CACHE_B64")


def build_config():
    from longbridge.openapi import Config

    for key in tuple(os.environ):
        if key.startswith("LONGBRIDGE_") and not os.environ[key].strip():
            del os.environ[key]
    os.environ.setdefault("LONGBRIDGE_PRINT_QUOTE_PACKAGES", "false")
    region = value("LONGBRIDGE_REGION").lower()
    if region:
        if region not in ("cn", "hk"):
            raise ValueError("LONGBRIDGE_REGION 仅支持 cn 或 hk")
        os.environ["LONGPORT_REGION"] = region
        domain = "longbridge.cn" if region == "cn" else "longbridge.com"
        for name, url in {
            "HTTP": f"https://openapi.{domain}",
            "QUOTE_WS": f"wss://openapi-quote.{domain}/v2",
            "TRADE_WS": f"wss://openapi-trade.{domain}/v2",
        }.items():
            os.environ.setdefault(f"LONGBRIDGE_{name}_URL", url)
    extra = {name: value(f"LONGBRIDGE_{name.upper()}")
             for name in ("http_url", "quote_ws_url", "trade_ws_url")
             if value(f"LONGBRIDGE_{name.upper()}")}
    extra["enable_print_quote_packages"] = False
    key, secret, token = (value(f"LONGBRIDGE_{name}")
                          for name in ("APP_KEY", "APP_SECRET", "ACCESS_TOKEN"))
    client = value("LONGBRIDGE_OAUTH_CLIENT_ID") or (key if not token else "")
    if client:
        try:
            from longbridge.openapi import OAuthBuilder

            if client in (".", "..") or any(c in client for c in ("/", "\\", ":")):
                raise ValueError("OAuth client id 格式无效")
            cache = Path.home() / ".longbridge" / "openapi" / "tokens" / client
            encoded = value("LONGBRIDGE_OAUTH_TOKEN_CACHE_B64")
            # Keep any SDK-refreshed token during this run instead of restoring stale tokens.
            if encoded and not cache.exists():
                payload = base64.b64decode("".join(encoded.split()), validate=True)
                decoded = json.loads(payload)
                if not isinstance(decoded, dict) or not decoded:
                    raise ValueError("OAuth token cache 必须为非空 JSON 对象")
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(payload)
                cache.chmod(0o600)
            if not cache.exists() or not isinstance(json.loads(cache.read_bytes()), dict):
                raise ValueError("缺少有效 OAuth token cache")
            oauth = OAuthBuilder(client).build(reject_interactive_auth)
            return Config.from_oauth(oauth, **extra)
        except Exception as exc:
            logger.warning("Longbridge OAuth 初始化失败（%s），尝试完整 API Key 三件套", type(exc).__name__)
            if not all((key, secret, token)):
                raise RuntimeError("OAuth 不可用，请检查缓存与 SDK 版本") from None
    if not all((key, secret, token)):
        raise ValueError("Longbridge 需要完整 API Key 三件套或 OAuth client 与 token cache")
    return Config.from_apikey(key, secret, token, **extra)


def get_name(stock: Stock) -> str:
    from longbridge.openapi import QuoteContext

    infos = QuoteContext(build_config()).static_info([stock.longbridge_symbol])
    if infos:
        return infos[0].name_cn or infos[0].name_en or ""
    return ""


def fetch(stock: Stock, start: date, end: date) -> list[DailyBar]:
    from longbridge.openapi import AdjustType, Period, QuoteContext
    from zoneinfo import ZoneInfo

    ctx = QuoteContext(build_config())
    candles = ctx.history_candlesticks_by_date(
        stock.longbridge_symbol, Period.Day, AdjustType.NoAdjust, start, end)
    bars = []
    for candle in candles or []:
        timestamp = candle.timestamp
        if isinstance(timestamp, datetime):
            day = (timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
                   if timestamp.tzinfo else timestamp).date()
        else:
            day = datetime.fromtimestamp(int(timestamp), ZoneInfo("Asia/Shanghai")).date()
        bars.append(DailyBar(day, *(float(getattr(candle, key))
                                   for key in ("open", "high", "low", "close", "volume")),
                             float(candle.turnover) if candle.turnover is not None else None))
    return bars
