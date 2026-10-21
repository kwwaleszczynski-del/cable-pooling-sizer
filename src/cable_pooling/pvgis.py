"""Cross-check of ERA5 irradiance against PVGIS-SARAH3 satellite data (JRC PVGIS API v5.3, no key).

ERA5 cloud cover is coarse, so its surface irradiance can be biased. PVGIS ``MRcalc`` returns monthly global
horizontal irradiation H(h) from the SARAH-3 satellite record (up to 2023); the ratio ERA5 / SARAH-3 over the
same months shows whether the PV yields need a correction.

Usage:
    m = fetch_monthly(site, 2020, 2023, cache_dir=Path("data/pvgis"))
    check = compare(m, hourly_era5)       # month-level ERA5 and SARAH-3 irradiation, kWh/m2
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import requests

from cable_pooling.config import Site

URL = "https://re.jrc.ec.europa.eu/api/v5_3/MRcalc"
log = logging.getLogger(__name__)


def parse_monthly(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["outputs"]["monthly"])
    return df.rename(columns={"H(h)_m": "sarah3_kwh_m2"})[["year", "month", "sarah3_kwh_m2"]]


def fetch_monthly(site: Site, start_year: int, end_year: int, cache_dir: Path, refresh: bool = False) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"pvgis_mr_{site.key}_{site.latitude:.2f}_{site.longitude:.2f}_{start_year}_{end_year}.json"
    if not path.exists() or refresh:
        params = {"lat": site.latitude, "lon": site.longitude, "horirrad": 1,
                  "startyear": start_year, "endyear": end_year, "outputformat": "json"}
        log.info("PVGIS MRcalc %s %d-%d", site.key, start_year, end_year)
        r = requests.get(URL, params=params, timeout=60)
        r.raise_for_status()
        path.write_text(r.text, encoding="utf-8")
    return parse_monthly(json.loads(path.read_text(encoding="utf-8")))


def compare(monthly: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    """Join PVGIS monthly irradiation with ERA5 GHI summed per UTC month (kWh/m2)."""
    ghi = hourly["ghi"] / 1000.0
    era5 = ghi.groupby([ghi.index.year, ghi.index.month]).sum()
    era5.index.names = ["year", "month"]
    out = monthly.merge(era5.rename("era5_kwh_m2").reset_index(), on=["year", "month"], how="inner")
    out["ratio"] = out["era5_kwh_m2"] / out["sarah3_kwh_m2"]
    return out
