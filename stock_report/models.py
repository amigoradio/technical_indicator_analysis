"""Provider-independent daily bar contract. Prices are unadjusted; volume is shares."""

from dataclasses import dataclass
from datetime import date
from math import isfinite
import re


@dataclass(frozen=True)
class Stock:
    code: str
    market: str

    @classmethod
    def parse(cls, raw: str) -> "Stock":
        code = raw.strip().lower()
        if re.fullmatch(r"hk\d{1,5}", code):
            return cls("hk" + code[2:].zfill(5), "HK")
        # Six-digit mainland symbols include stocks, ETFs and other listed funds;
        # their prefixes are not limited to 0/3/6 (for example 511090, 159870).
        if re.fullmatch(r"\d{6}", code):
            return cls(code, "CN")
        raise ValueError("股票代码应为六位 A 股代码或 hk 前缀的港股代码")

    @property
    def exchange(self) -> str:
        if self.market == "HK":
            return "HK"
        if self.code.startswith(("5", "6")):
            return "SH"
        if self.code.startswith(("4", "8", "92")):
            return "BJ"
        return "SZ"

    @property
    def is_etf(self) -> bool:
        """Mainland ETF code ranges used by the Shanghai/Shenzhen exchanges."""
        return self.market == "CN" and self.code.startswith(("51", "52", "56", "58", "15", "16", "18"))

    @property
    def asset_type(self) -> str:
        """Stable instrument marker used by future fundamental/valuation layers."""
        return "ETF" if self.is_etf else "STOCK"

    @property
    def supports_fundamentals(self) -> bool:
        """ETFs have no stock EPS/PE/net-profit fundamentals in this pipeline."""
        return not self.is_etf

    @property
    def adjustment(self) -> str:
        """Daily bars are always unadjusted; ETFs must never use dividend adjustment."""
        return "none"

    @property
    def longbridge_symbol(self) -> str:
        code = self.code[2:] if self.market == "HK" else self.code
        return f"{code}.{self.exchange}"


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    instrument_name: str | None = None


def validate_bars(bars: list[DailyBar], target: date) -> list[DailyBar]:
    """Reject corrupt responses before accepting a provider as successful."""
    by_date = {}
    for bar in bars:
        if bar.date > target:
            continue
        prices = (bar.open, bar.high, bar.low, bar.close)
        if not all(isfinite(p) and p > 0 for p in prices):
            raise ValueError("日 K 价格为空或无效")
        if not (bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
            raise ValueError("日 K 高低价不一致")
        if not isfinite(bar.volume) or bar.volume < 0:
            raise ValueError("成交量无效")
        if bar.amount is not None and (not isfinite(bar.amount) or bar.amount < 0):
            raise ValueError("成交额无效")
        if bar.date in by_date:
            raise ValueError("日 K 日期重复")
        by_date[bar.date] = bar
    ordered = sorted(by_date.values(), key=lambda bar: bar.date)
    if not ordered or ordered[-1].date != target:
        latest = str(ordered[-1].date) if ordered else "无"
        raise ValueError(f"缺少当日日 K（最近数据日期：{latest}；可能休市、停牌或尚未更新）")
    return ordered
