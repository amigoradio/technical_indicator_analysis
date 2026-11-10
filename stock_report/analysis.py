"""Pure calculations on DailyBar objects; no SDK, configuration or network imports."""

from dataclasses import dataclass

from .models import DailyBar


@dataclass(frozen=True)
class Analysis:
    latest: DailyBar
    change_pct: float | None
    moving_averages: dict[int, float | None]


def analyze(bars: list[DailyBar]) -> Analysis:
    if not bars:
        raise ValueError("分析需要至少一根日 K")
    ordered = sorted(bars, key=lambda bar: bar.date)
    latest = ordered[-1]
    previous = ordered[-2].close if len(ordered) > 1 else None
    return Analysis(latest, (latest.close / previous - 1) * 100 if previous else None,
                    {period: sum(b.close for b in ordered[-period:]) / period
                     if len(ordered) >= period else None for period in (5, 10, 20)})
