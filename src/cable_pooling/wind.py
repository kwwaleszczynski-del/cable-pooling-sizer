"""Hourly output of a wind farm per MW installed, from ERA5 wind speed and a Senvion MM92 power curve.

The power curve is applied to the instantaneous hourly wind speeds (after optional extrapolation to hub height
with the hourly shear exponent between 10 m and 100 m), and the two ends of each hour are averaged, so the
non-linear curve sees real values rather than a smoothed mean. Farm losses (wakes, availability, electrical)
are a flat factor.

Usage:
    ws, pu = load_power_curve()
    wind = wind_power_per_mw(raw["wind_100m"], raw["wind_10m"], WindParams())
"""
from __future__ import annotations

from importlib import resources

import numpy as np
import pandas as pd

from cable_pooling.config import WindParams
from cable_pooling.openmeteo import instant_to_interval_mean

RATED_KW = 2050.0
CURVE_FILE = "mm92_kelmarsh_2017.csv"
SHEAR_CLIP = (0.0, 0.45)


def load_power_curve() -> tuple[np.ndarray, np.ndarray]:
    """Wind speed [m/s] and power as a fraction of rated, from the packaged empirical MM92 curve."""
    with resources.files("cable_pooling").joinpath("data", CURVE_FILE).open("r", encoding="utf-8") as f:
        df = pd.read_csv(f, comment="#")
    return df["ws_ms"].to_numpy(dtype=float), (df["power_kw"] / RATED_KW).clip(0.0, 1.0).to_numpy(dtype=float)


def power_per_unit(ws, curve_ws: np.ndarray, curve_pu: np.ndarray, cut_out: float) -> np.ndarray:
    """Linear interpolation of the curve; zero above cut-out and for missing speeds."""
    ws = np.nan_to_num(np.asarray(ws, dtype=float), nan=0.0)
    p = np.interp(ws, curve_ws, curve_pu, left=0.0, right=0.0)
    p[ws > cut_out] = 0.0
    return p


def hub_height_speed(ws100, ws10, hub_height_m: float) -> np.ndarray:
    """Power-law extrapolation from 100 m with the hourly exponent alpha = ln(v100/v10) / ln(10)."""
    ws100 = np.asarray(ws100, dtype=float)
    if hub_height_m == 100.0:
        return ws100
    ws10 = np.asarray(ws10, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = np.log(ws100 / ws10) / np.log(10.0)
    alpha = np.where((ws10 > 0.5) & (ws100 > 0.5) & np.isfinite(alpha), alpha, 1.0 / 7.0)
    alpha = np.clip(alpha, *SHEAR_CLIP)
    return ws100 * (hub_height_m / 100.0) ** alpha


def wind_power_per_mw(ws100: pd.Series, ws10: pd.Series, params: WindParams,
                      curve: tuple[np.ndarray, np.ndarray] | None = None) -> pd.Series:
    """MW per MW installed for hourly intervals indexed by their start (UTC), from instantaneous speeds."""
    curve_ws, curve_pu = curve or load_power_curve()
    ws_hub = hub_height_speed(ws100.to_numpy(), ws10.to_numpy(), params.hub_height_m)
    p_inst = pd.Series(power_per_unit(ws_hub, curve_ws, curve_pu, params.cut_out_ms), index=ws100.index)
    return (instant_to_interval_mean(p_inst) * (1.0 - params.losses)).dropna().rename("wind")
