"""RCE prices ("rynkowa cena energii", PLN/MWh) from the public PSE reports API (https://api.raporty.pse.pl/api).

The ``rce-pln`` report has 15-minute prices since 14 June 2024. ``dtime_utc`` is the END of each interval
("00:00 - 00:15" -> 00:15). Results are paginated (``$first`` up to 20,000 records, then ``nextLink``).
Complete past months are cached as Parquet.

Usage:
    q = fetch_range(date(2024, 10, 1), date(2026, 9, 30), cache_dir=Path("data/prices"))
    hourly = to_hourly(q)        # mean price per UTC hour, indexed by the hour start
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.raporty.pse.pl/api"
ENDPOINT, FIELD = "rce-pln", "rce_pln"
QUARTER = pd.Timedelta("15min")
log = logging.getLogger(__name__)


def parse_records(records: list[dict]) -> pd.DataFrame:
    """API records -> frame indexed by interval START in UTC with columns price and business_date."""
    if not records:
        empty = pd.DatetimeIndex([], tz="UTC", name="ts_utc").as_unit("ns")
        return pd.DataFrame({"price": pd.Series(dtype=float), "business_date": pd.Series(dtype=object)}, index=empty)
    df = pd.DataFrame.from_records(records)
    start = pd.DatetimeIndex(pd.to_datetime(df["dtime_utc"])).tz_localize("UTC").as_unit("ns") - QUARTER
    out = pd.DataFrame({
        "price": pd.to_numeric(df[FIELD], errors="coerce").to_numpy(dtype=float),
        "business_date": pd.to_datetime(df["business_date"]).dt.date.to_numpy(),
    }, index=start.rename("ts_utc"))
    return out[~out.index.duplicated()].sort_index()


def _get(session: requests.Session, url: str, params: dict | None = None, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            log.warning("PSE API request failed (%s), retrying", e)
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def fetch_period(start: date, end: date, session: requests.Session | None = None) -> pd.DataFrame:
    """RCE for business dates start..end (inclusive), following pagination links."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "cable-pooling-sizer (open-source analysis)")
    params = {
        "$filter": f"business_date ge '{start:%Y-%m-%d}' and business_date le '{end:%Y-%m-%d}'",
        "$select": f"dtime_utc,{FIELD},business_date",
        "$first": 20000,
    }
    payload = _get(session, f"{BASE_URL}/{ENDPOINT}", params)
    records = list(payload.get("value", []))
    while payload.get("nextLink"):
        payload = _get(session, payload["nextLink"])
        records += payload.get("value", [])
    return parse_records(records)


def fetch_range(start: date, end: date, cache_dir: Path, refresh: bool = False) -> pd.DataFrame:
    """Fetch start..end month by month; complete past months are cached."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    frames = []
    for month_start in pd.date_range(date(start.year, start.month, 1), end, freq="MS"):
        m_start = max(month_start.date(), start)
        m_end = min((month_start + pd.offsets.MonthEnd(0)).date(), end)
        cache = cache_dir / f"rce_{month_start:%Y-%m}.parquet"
        complete = (month_start + pd.offsets.MonthEnd(0)).date() < date.today()
        if cache.exists() and not refresh and complete:
            df = pd.read_parquet(cache)
        else:
            df = fetch_period(month_start.date(), (month_start + pd.offsets.MonthEnd(0)).date(), session)
            if complete:
                df.to_parquet(cache)
            log.info("RCE %s: %d intervals", f"{month_start:%Y-%m}", len(df))
        frames.append(df[(df["business_date"] >= m_start) & (df["business_date"] <= m_end)])
    return pd.concat(frames).sort_index() if frames else parse_records([])


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
