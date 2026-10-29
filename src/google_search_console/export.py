"""Write Search Analytics rows to CSV or a pandas DataFrame."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import IO, Any, Iterable, List, Sequence, Union

from ._api import Row

METRICS = ("clicks", "impressions", "ctr", "position")


def _columns(rows: Sequence[Row]) -> List[str]:
    dims = list(rows[0].dimensions) if rows else []
    return dims + list(METRICS)


def to_csv(rows: Iterable[Row], dest: Union[str, Path, IO[str], None] = None, *,
           encoding: str = "utf-8") -> str:
    """Write rows as CSV (one column per dimension, then the metrics).

    ``dest`` may be a path or an open text file; the CSV text is also returned.
    Use ``encoding="utf-8-sig"`` if the file will be opened in Excel."""
    rows = list(rows)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_columns(rows), lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r.as_dict())
    text = buf.getvalue()
    if isinstance(dest, (str, Path)):
        with open(dest, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
    elif dest is not None:
        dest.write(text)
    return text


def to_dataframe(rows: Iterable[Row]) -> Any:
    """A pandas DataFrame (``pip install google-search-console[pandas]``)."""
    try:
        import pandas as pd  # type: ignore[import-untyped,import-not-found,unused-ignore]
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError("to_dataframe() needs pandas: "
                          "pip install 'google-search-console[pandas]'") from exc
    rows = list(rows)
    df = pd.DataFrame([r.as_dict() for r in rows], columns=_columns(rows))
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df
