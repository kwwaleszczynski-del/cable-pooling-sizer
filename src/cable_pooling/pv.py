"""Hourly AC output of a fixed-tilt PV plant per MWp, from ERA5 irradiance with pvlib.

Chain: solar position at the middle of each hour -> DNI from GHI and DHI -> Perez transposition to the plane of
array -> incidence angle modifier on the beam -> SAPM cell temperature -> PVWatts DC power with temperature
coefficient and DC losses -> PVWatts inverter, clipped at the AC rating (MWp / DC-AC ratio).

Usage:
    pv = pv_power_per_mwp(hourly, latitude=52.75, longitude=18.25, elevation_m=88, params=PVParams())
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib

from cable_pooling.config import PVParams

HALF_HOUR = pd.Timedelta("30min")
TEMP_PARAMS = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]


def pv_power_per_mwp(hourly: pd.DataFrame, latitude: float, longitude: float, elevation_m: float,
                     params: PVParams) -> pd.Series:
    """MW AC per MWp for hourly intervals indexed by their start (UTC); needs ghi, dhi, temp_air, wind_10m."""
    mid = hourly.index + HALF_HOUR
    solpos = pvlib.solarposition.get_solarposition(mid, latitude, longitude, altitude=elevation_m)
    zenith = solpos["apparent_zenith"].to_numpy()
    ghi = hourly["ghi"].to_numpy(dtype=float)
    dhi = np.minimum(hourly["dhi"].to_numpy(dtype=float), ghi)
    dni = pvlib.irradiance.dni(ghi, dhi, zenith, zenith_threshold_for_zero_dni=88.0)
    dni = np.nan_to_num(np.asarray(dni, dtype=float), nan=0.0).clip(min=0.0)
    dni_extra = pvlib.irradiance.get_extra_radiation(mid).to_numpy()
    airmass = pvlib.atmosphere.get_relative_airmass(zenith)
    poa = pvlib.irradiance.get_total_irradiance(
        params.tilt_deg, params.azimuth_deg, zenith, solpos["azimuth"].to_numpy(), dni, ghi, dhi,
        dni_extra=dni_extra, airmass=airmass, albedo=params.albedo, model="perez")
    aoi = pvlib.irradiance.aoi(params.tilt_deg, params.azimuth_deg, zenith, solpos["azimuth"].to_numpy())
    iam = pvlib.iam.physical(aoi)
    poa_eff = np.nan_to_num(np.asarray(poa["poa_direct"]) * iam + np.asarray(poa["poa_diffuse"]), nan=0.0)
    poa_eff = poa_eff.clip(min=0.0)
    t_cell = pvlib.temperature.sapm_cell(poa_eff, hourly["temp_air"].to_numpy(), hourly["wind_10m"].to_numpy(),
                                         **TEMP_PARAMS)
    pdc = pvlib.pvsystem.pvwatts_dc(poa_eff, t_cell, 1.0, params.gamma_pdc) * (1.0 - params.dc_losses)
    ac_rating = 1.0 / params.dc_ac_ratio
    pac = pvlib.inverter.pvwatts(np.asarray(pdc, dtype=float), ac_rating / params.inverter_efficiency,
                                 eta_inv_nom=params.inverter_efficiency)
    pac = np.clip(np.nan_to_num(np.asarray(pac, dtype=float), nan=0.0), 0.0, ac_rating)
    return pd.Series(pac, index=hourly.index, name="pv")
