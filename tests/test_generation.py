import numpy as np
import pandas as pd
import pvlib
import pytest

from cable_pooling.config import PVParams, WindParams
from cable_pooling.pv import pv_power_per_mwp
from cable_pooling.wind import hub_height_speed, load_power_curve, power_per_unit, wind_power_per_mw


def test_power_curve_shape():
    ws, pu = load_power_curve()
    assert pu.max() == pytest.approx(1.0, abs=0.01)
    assert np.all(pu[ws <= 2.5] == 0)
    rising = pu[(ws >= 3) & (ws <= 13)]
    assert np.all(np.diff(rising) > 0)
    assert ws.max() == 25.0


def test_power_per_unit_cut_out_and_nan():
    ws, pu = load_power_curve()
    p = power_per_unit([0.0, 8.0, 15.0, 26.0, np.nan], ws, pu, cut_out=25.0)
    assert p[0] == 0 and 0.4 < p[1] < 0.6 and p[2] == pytest.approx(1.0, abs=0.01)
    assert p[3] == 0 and p[4] == 0


def test_hub_height_extrapolation():
    assert hub_height_speed([8.0], [6.0], 100.0)[0] == 8.0
    alpha = np.log(8.0 / 6.0) / np.log(10.0)
    assert hub_height_speed([8.0], [6.0], 120.0)[0] == pytest.approx(8.0 * 1.2 ** alpha)
    # calm 10 m wind: fall back to the 1/7 power law
    assert hub_height_speed([5.0], [0.2], 150.0)[0] == pytest.approx(5.0 * 1.5 ** (1 / 7))


def test_wind_interval_mean_of_instants():
    idx = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    ws100 = pd.Series([0.0, 30.0, 15.0], index=idx)     # calm, above cut-out, rated
    ws10 = pd.Series([0.0, 20.0, 10.0], index=idx)
    out = wind_power_per_mw(ws100, ws10, WindParams(losses=0.0))
    assert len(out) == 2
    assert out.iloc[0] == pytest.approx(0.0)
    assert out.iloc[1] == pytest.approx(0.5, abs=0.01)  # (0 + 1) / 2
    lossy = wind_power_per_mw(ws100, ws10, WindParams(losses=0.1))
    assert lossy.iloc[1] == pytest.approx(0.9 * out.iloc[1])


def clear_sky_hours(day: str, lat=52.0, lon=19.0):
    idx = pd.date_range(f"{day} 00:00", periods=24, freq="h", tz="UTC")
    mid = idx + pd.Timedelta("30min")
    zen = pvlib.solarposition.get_solarposition(mid, lat, lon)["apparent_zenith"]
    ghi = pvlib.clearsky.haurwitz(zen)["ghi"].to_numpy()
    return pd.DataFrame({"ghi": ghi, "dhi": 0.15 * ghi, "temp_air": 20.0, "wind_10m": 2.0}, index=idx)


def test_pv_clear_summer_day():
    params = PVParams()
    out = pv_power_per_mwp(clear_sky_hours("2025-06-21"), 52.0, 19.0, 100.0, params)
    assert out.min() >= 0 and out.max() <= 1 / params.dc_ac_ratio + 1e-9
    assert out.iloc[0] == 0 and out.iloc[23] == 0          # night (UTC 00 and 23)
    assert 5.0 < out.sum() < 9.0                            # kWh/kWp on a clear June day
    assert out.idxmax().hour in (9, 10, 11)                 # solar noon at 19 E is ~10:45 UTC


def test_pv_inverter_clipping():
    hours = clear_sky_hours("2025-06-21")
    hours[["ghi", "dhi"]] *= 1.3                            # over-irradiance pushes DC above the AC rating
    params = PVParams(dc_ac_ratio=1.5)
    out = pv_power_per_mwp(hours, 52.0, 19.0, 100.0, params)
    assert out.max() == pytest.approx(1 / 1.5, rel=1e-6)


def test_pv_winter_lower_than_summer():
    p = PVParams()
    summer = pv_power_per_mwp(clear_sky_hours("2025-06-21"), 52.0, 19.0, 100.0, p).sum()
    winter = pv_power_per_mwp(clear_sky_hours("2025-12-21"), 52.0, 19.0, 100.0, p).sum()
    assert winter < 0.5 * summer
