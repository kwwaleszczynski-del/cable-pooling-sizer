"""RCE prices ("rynkowa cena energii", PLN/MWh) of PSE, read with the pse-opendata client
(https://pypi.org/project/pse-opendata/).

The ``rce-pln`` report has 15-minute prices since 14 June 2024. pse-opendata downloads the public PSE reports API
(no key) and caches whole months as Parquet files in ``cache_dir`` (``api/rce-pln/<YYYY-MM>.parquet``); a month is
downloaded again until three days after its end (the revision window of prices).

Usage:
    q = fetch_range(date(2024, 10, 1), date(2026, 9, 30), cache_dir=Path("data/prices"))
    hourly = to_hourly(q)        # mean price per UTC hour, indexed by the hour start
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pse_opendata as pse

ENDPOINT, FIELD = "rce-pln", "rce_pln"
log = logging.getLogger(__name__)


def empty_frame() -> pd.DataFrame:
    empty = pd.DatetimeIndex([], tz="UTC", name="ts_utc").as_unit("ns")
    return pd.DataFrame({"price": pd.Series(dtype=float), "business_date": pd.Series(dtype=object)}, index=empty)


def fetch_range(start: date, end: date, cache_dir: Path, refresh: bool = False,
                client: pse.Client | None = None) -> pd.DataFrame:
    """RCE for business dates start..end (inclusive): columns price and business_date, index ts_utc (UTC interval
    start).

    Whole months are requested, so that pse-opendata caches them; ``refresh`` downloads again months the cache holds
    as final. Intervals without a price are kept; a repeated interval keeps its last row.
    """
    client = client or pse.Client(cache_dir=cache_dir, refresh=refresh)
    loaded = len(client.loads)
    raw = client.get(ENDPOINT, date(start.year, start.month, 1), (pd.Timestamp(end) + pd.offsets.MonthEnd(0)).date(),
                     fields=[FIELD])
    for load in client.loads[loaded:]:
        log.info("RCE %s: %d intervals (%s)", f"{load.month:%Y-%m}", load.rows, load.status)
    raw = raw[(raw["business_date"] >= start) & (raw["business_date"] <= end)]
    if raw.empty:
        return empty_frame()
    return pd.DataFrame({"price": raw[FIELD].to_numpy(dtype=float), "business_date": raw["business_date"].to_numpy()},
                        index=pd.DatetimeIndex(raw["start_utc"], name="ts_utc"))


def to_hourly(prices: pd.DataFrame) -> pd.Series:
    """Mean price per UTC hour (index = hour start). Hours with fewer than four quarters are dropped.

    With generation constant within the hour, the hourly mean gives exactly the energy-weighted revenue.
    """
    s = prices["price"].dropna()
    hours = s.index.floor("h")
    g = s.groupby(hours)
    hourly = g.mean()[g.count() == 4]
    hourly.index = pd.DatetimeIndex(hourly.index, name="ts_utc").as_unit("ns")
    return hourly.rename("price")


def economic_hours(start: date, end: date) -> pd.DatetimeIndex:
    """UTC hour starts covering business dates start..end in Europe/Warsaw (DST-aware)."""
    first = pd.Timestamp(start).tz_localize("Europe/Warsaw").tz_convert("UTC")
    last = pd.Timestamp(end + timedelta(days=1)).tz_localize("Europe/Warsaw").tz_convert("UTC")
    return pd.date_range(first, last, freq="h", inclusive="left", name="ts_utc").as_unit("ns")
