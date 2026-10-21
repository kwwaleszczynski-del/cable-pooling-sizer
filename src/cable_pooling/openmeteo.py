"""Hourly ERA5 reanalysis from the Open-Meteo Historical Weather API (no API key).

Open-Meteo counts a request as ``max(1, n_vars / 10 * max(1, n_days / 14))`` calls per location and allows
600 calls/min, 5,000/h and 10,000/day per IP address on the free tier. One request per site covers the whole
period; the response is cached as Parquet and never requested again unless ``refresh=True``. Every request
is written to a ledger (``openmeteo_ledger.json`` in the cache directory) and refused when it would push the
last 24 hours above ``max_daily_weight``.

Time convention: radiation is the mean of the hour that ENDS at the timestamp ("preceding hour mean"),
temperature and wind speed are instantaneous values at the timestamp. ``to_intervals`` converts both to
hourly intervals indexed by their START, the convention used everywhere else in the package.

Usage:
    raw = fetch_site(site, date(2020, 1, 1), date(2026, 9, 30), cache_dir=Path("data/weather"))
    hourly = to_intervals(raw)      # ghi, dhi, temp_air, wind_10m per interval start (UTC)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from cable_pooling.config import Site

URL = "https://archive-api.open-meteo.com/v1/archive"
MODEL = "era5"
VARIABLES = {
    "shortwave_radiation": "ghi",       # W/m2, preceding-hour mean
    "diffuse_radiation": "dhi",         # W/m2, preceding-hour mean
    "temperature_2m": "temp_air",       # deg C, instantaneous
    "wind_speed_10m": "wind_10m",       # m/s, instantaneous
    "wind_speed_100m": "wind_100m",     # m/s, instantaneous
}
RADIATION = ("ghi", "dhi")
HOUR = pd.Timedelta("1h")
log = logging.getLogger(__name__)


def request_weight(n_vars: int, start: date, end: date) -> float:
    """Open-Meteo's cost of one request for one location, in 'calls'."""
    n_days = (end - start).days + 1
    return max(1.0, n_vars / 10.0 * max(1.0, n_days / 14.0))


def parse_response(payload: dict) -> pd.DataFrame:
    """Hourly response -> frame indexed by the UTC timestamp of each value, columns renamed (see VARIABLES)."""
    hourly = payload["hourly"]
    index = pd.DatetimeIndex(pd.to_datetime(hourly["time"]), name="stamp_utc").tz_localize("UTC").as_unit("ns")
    df = pd.DataFrame({VARIABLES[k]: pd.to_numeric(pd.Series(hourly[k]), errors="coerce").to_numpy(dtype=float)
                       for k in VARIABLES}, index=index)
    df["elevation_m"] = float(payload.get("elevation", np.nan))
    return df


class _Ledger:
    """Weights of past requests with their time, to stay well below the per-IP daily limit."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: list[list] = json.loads(path.read_text()) if path.exists() else []

    def last_24h(self) -> float:
        now = time.time()
        return float(sum(e[1] for e in self.entries if now - e[0] < 86400))

    def add(self, weight: float, label: str) -> None:
        self.entries.append([time.time(), weight, label])
        self.path.write_text(json.dumps(self.entries))


def cache_path(cache_dir: Path, site: Site, start: date, end: date) -> Path:
    return cache_dir / f"era5_{site.key}_{site.latitude:.2f}_{site.longitude:.2f}_{start:%Y%m%d}_{end:%Y%m%d}.parquet"


def fetch_site(site: Site, start: date, end: date, cache_dir: Path, refresh: bool = False,
               max_daily_weight: float = 1000.0, session: requests.Session | None = None) -> pd.DataFrame:
    """Raw hourly ERA5 series for one site (cached). Raises instead of retrying when rate-limited."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, site, start, end)
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    ledger = _Ledger(cache_dir / "openmeteo_ledger.json")
    weight = request_weight(len(VARIABLES), start, end)
    if ledger.last_24h() + weight > max_daily_weight:
        raise RuntimeError(f"Open-Meteo budget: {ledger.last_24h():.0f} used in 24 h + {weight:.0f} requested "
                           f"> {max_daily_weight:.0f}; try later or raise --max-weight")
    params = {
        "latitude": site.latitude, "longitude": site.longitude,
        "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}",
        "hourly": ",".join(VARIABLES), "models": MODEL,
        "timezone": "GMT", "wind_speed_unit": "ms",
    }
    session = session or requests.Session()
    log.info("Open-Meteo ERA5 %s %s..%s (weight %.0f)", site.key, start, end, weight)
    r = session.get(URL, params=params, timeout=300)
    ledger.add(weight, f"{site.key} {start}..{end}")
    if r.status_code == 429:
        raise RuntimeError(f"Open-Meteo rate limit reached: {r.text[:200]}")
    r.raise_for_status()
    df = parse_response(r.json())
    df.to_parquet(path)
    return df


def instant_to_interval_mean(s: pd.Series) -> pd.Series:
    """Instantaneous values at full hours -> mean over [t, t + 1 h), indexed by the interval start t."""
    return ((s + s.shift(-1, freq=HOUR).reindex(s.index)) / 2.0).rename(s.name)


def to_intervals(raw: pd.DataFrame) -> pd.DataFrame:
    """Raw stamped series -> hourly intervals indexed by their START (UTC).

    Radiation stamped T describes [T - 1 h, T) and moves to T - 1 h; instantaneous values are averaged over
    the two ends of each interval.
    """
    out = pd.DataFrame(index=raw.index)
    for col in RADIATION:
        out[col] = raw[col].shift(-1, freq=HOUR).reindex(raw.index).clip(lower=0.0)
    for col in ("temp_air", "wind_10m"):
        out[col] = instant_to_interval_mean(raw[col])
    return out.dropna()
