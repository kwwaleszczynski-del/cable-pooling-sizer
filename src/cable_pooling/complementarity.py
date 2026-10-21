"""How well PV and wind fill each other's gaps at one site, and what the market pays for each.

Usage:
    stats = summary(prof.hourly, prof.economic)
    prof_day = diurnal_profiles(prof.hourly)      # mean per-unit output by season and local hour
"""
from __future__ import annotations

import pandas as pd

LOCAL_TZ = "Europe/Warsaw"
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def full_years(hourly: pd.DataFrame) -> pd.DataFrame:
    counts = hourly.groupby(hourly.index.year).size()
    keep = counts[counts >= 8760].index
    return hourly[hourly.index.year.isin(keep)]


def correlations(hourly: pd.DataFrame) -> dict:
    """Pearson correlation of PV and wind output at hourly, daily and monthly resolution."""
    local = hourly[["pv", "wind"]].tz_convert(LOCAL_TZ)
    daily = local.resample("D").mean()
    monthly = local.resample("MS").mean()
    return {"corr_hourly": float(local["pv"].corr(local["wind"])),
            "corr_daily": float(daily["pv"].corr(daily["wind"])),
            "corr_monthly": float(monthly["pv"].corr(monthly["wind"]))}


def diurnal_profiles(hourly: pd.DataFrame) -> pd.DataFrame:
    """Mean output per unit by season and local hour of day (columns pv, wind)."""
    local = hourly[["pv", "wind"]].tz_convert(LOCAL_TZ)
    season = local.index.month.map(SEASONS)
    return local.groupby([pd.Index(season, name="season"), pd.Index(local.index.hour, name="hour")]).mean()


def monthly_cf(hourly: pd.DataFrame) -> pd.DataFrame:
    """Mean capacity factor per calendar month (columns pv, wind), PV per MWp."""
    local = hourly[["pv", "wind"]].tz_convert(LOCAL_TZ)
    return local.groupby(pd.Index(local.index.month, name="month")).mean()


def annual_cf(hourly: pd.DataFrame) -> pd.DataFrame:
    """Capacity factor per full calendar year (UTC)."""
    full = full_years(hourly[["pv", "wind"]])
    return full.groupby(pd.Index(full.index.year, name="year")).mean()


def capture(economic: pd.DataFrame) -> dict:
    """Capture prices (generation-weighted RCE) of PV and wind vs the time-weighted average price."""
    p = economic["price"]
    base = float(p.mean())
    out = {"baseload_price": base}
    for tech in ("pv", "wind"):
        g = economic[tech]
        cp = float((g * p).sum() / g.sum())
        out[f"{tech}_capture_price"] = cp
        out[f"{tech}_capture_rate"] = cp / base
        out[f"{tech}_share_negative_price"] = float(g[p < 0].sum() / g.sum())
    return out


def summary(hourly: pd.DataFrame, economic: pd.DataFrame) -> dict:
    full = full_years(hourly)
    out = {"pv_yield_kwh_per_kwp": float(full["pv"].sum() / full.index.year.nunique()),
           "wind_cf": float(full["wind"].mean())}
    out.update(correlations(hourly))
    out.update(capture(economic))
    # hours in which a 1 MWp + 1 MW plant on a 1 MW connection would exceed the connection
    combined = economic["pv"] + economic["wind"]
    out["share_hours_1to1_above_grid"] = float((combined > 1.0).mean())
    return out
