"""Persistence for dated CSV datasets. Fetching and indicators belong to callers."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd


def parse_dates(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values.astype(str).str.strip(), format="mixed", errors="coerce").dt.normalize()


@dataclass(frozen=True)
class CsvStore:
    path: Path
    date_column: str
    columns: tuple[str, ...]
    float_digits: int = 8

    def _normalize(self, frame: pd.DataFrame, *, strict: bool) -> pd.DataFrame:
        frame = frame.copy()
        frame[self.date_column] = parse_dates(frame[self.date_column])
        if strict and frame[self.date_column].isna().any():
            raise ValueError(f"{self.path.name} 含无效日期")
        return (frame.dropna(subset=[self.date_column])
                .drop_duplicates(subset=[self.date_column], keep="last")
                .sort_values(self.date_column, ignore_index=True))

    def read(self, *, repair: bool = False) -> pd.DataFrame:
        """Read local data only, with parsed dates, one row per date and ascending order."""
        if not self.path.exists():
            return self._normalize(pd.DataFrame(columns=self.columns), strict=True)
        try:
            frame = pd.read_csv(self.path, encoding="utf-8-sig",
                                dtype={self.date_column: str})
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame(columns=self.columns)
        # Keep files created by the original margin prototype readable.
        frame = frame.rename(columns={"3年百分比": "融资余额3年百分比",
                                      "5年百分比": "融资余额5年百分比"})
        if not repair and tuple(frame.columns) != self.columns:
            raise ValueError(f"{self.path.name} 表头不匹配，请运行本地校验修复")
        return self._normalize(frame.reindex(columns=self.columns), strict=not repair)

    def sync(
        self,
        incoming: pd.DataFrame,
        *,
        start: date,
        end: date,
        expected_dates: Iterable[date],
        derive: Callable[[pd.DataFrame], pd.DataFrame],
        replace: bool = False,
        existing: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Validate coverage, upsert or replace, trim the date range and save atomically."""
        incoming = self._normalize(incoming, strict=True)
        expected = pd.DatetimeIndex(list(expected_dates))
        actual = pd.DatetimeIndex(incoming[self.date_column])
        missing = expected.difference(actual)
        unexpected = actual.difference(expected)
        if len(missing) or len(unexpected):
            raise ValueError(f"{self.path.name} 日期不匹配：缺少 {missing.date.tolist()}，"
                             f"多出 {unexpected.date.tolist()}")
        if not replace:
            existing = self.read() if existing is None else existing
            if not existing.empty:
                incoming = self._normalize(
                    pd.concat([existing, incoming], ignore_index=True), strict=True)
        retained = incoming.loc[incoming[self.date_column].between(
            pd.Timestamp(start), pd.Timestamp(end))].reset_index(drop=True)
        result = derive(retained).loc[:, list(self.columns)]
        self._write(result)
        return result

    def _write(self, frame: pd.DataFrame) -> None:
        # UTF-8 BOM keeps Chinese headers readable in Excel and Windows tools.
        # Final write-time guard protects callers that bypass sync().
        frame = self._normalize(frame, strict=True)
        content = frame.to_csv(index=False, date_format="%Y-%m-%d",
                               float_format=f"%.{self.float_digits}f",
                               lineterminator="\n").encode("utf-8-sig")
        if self.path.exists() and self.path.read_bytes() == content:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=self.path.parent, delete=False) as temporary:
            staged = Path(temporary.name)
            try:
                temporary.write(content)
                temporary.close()
                staged.replace(self.path)
            finally:
                temporary.close()
                staged.unlink(missing_ok=True)
