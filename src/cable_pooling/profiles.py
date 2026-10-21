"""Per-site hourly profiles: PV per MWp and wind per MW over the weather period, plus prices for the economic window.

Usage:
    prof = site_profiles(cfg, site, data_dir=Path("data"))
    prof.hourly           # pv, wind for every hour of the weather period (UTC interval starts)
    prof.economic         # pv, wind, price for the economic window
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from cable_pooling import openmeteo, pse_api
from cable_pooling.config import Config, Site
from cable_pooling.pv import pv_power_per_mwp
from cable_pooling.wind import wind_power_per_mw

log = logging.getLogger(__name__)


@dataclass
class SiteProfiles:
    site: Site
    elevation_m: float
    weather: pd.DataFrame      # interval weather: ghi, dhi, temp_air, wind_10m
    hourly: pd.DataFrame       # pv, wind per unit, weather period
    economic: pd.DataFrame     # pv, wind, price, economic window


def load_prices(cfg: Config, data_dir: Path, refresh: bool = False) -> pd.Series:
    """Hourly RCE over the economic window; small gaps are interpolated, larger ones are an error."""
    q = pse_api.fetch_range(cfg.economics_start, cfg.economics_end, data_dir / "prices", refresh=refresh)
    idx = pse_api.economic_hours(cfg.economics_start, cfg.economics_end)
    price = pse_api.to_hourly(q).reindex(idx)
    missing = int(price.isna().sum())
    if missing > 24:
        raise RuntimeError(f"{missing} hours of RCE missing in the economic window")
    if missing:
        log.warning("interpolating %d missing price hours", missing)
        price = price.interpolate(limit_direction="both")
    return price


def site_profiles(cfg: Config, site: Site, data_dir: Path, prices: pd.Series, refresh: bool = False,
                  max_weight: float = 1000.0) -> SiteProfiles:
    raw = openmeteo.fetch_site(site, cfg.weather_start, cfg.weather_end, data_dir / "weather", refresh=refresh,
                               max_daily_weight=max_weight)
    elevation = float(raw["elevation_m"].iloc[0])
    weather = openmeteo.to_intervals(raw)
    pv = pv_power_per_mwp(weather, site.latitude, site.longitude, elevation, cfg.pv)
    wind = wind_power_per_mw(raw["wind_100m"], raw["wind_10m"], cfg.wind)
    hourly = pd.concat([pv, wind], axis=1).dropna()
    economic = hourly.reindex(prices.index)
    if economic.isna().any().any():
        raise RuntimeError(f"{site.key}: weather does not cover the economic window")
    economic["price"] = prices
    return SiteProfiles(site, elevation, weather, hourly, economic)
