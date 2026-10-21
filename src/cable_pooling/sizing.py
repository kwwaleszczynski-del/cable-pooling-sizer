"""Grid search over plant sizes behind a 1 MW connection: PV MWp, wind MW, battery MW and MWh.

Dispatch, curtailment and revenue are linear in the connection capacity G when every capacity is a multiple of G,
so the physics is simulated once per site with G = 1 MW and scaled afterwards; only the fixed part of the grid
connection cost (see ``economics``) differs between connection sizes.

Each row of the result holds annual averages over the economic window, per MW of connection.

Usage:
    phys = sweep(prof.economic, cfg.sizing, cfg.bess, workers=8)
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import numpy as np
import pandas as pd

from cable_pooling.config import BessTech, Sizing
from cable_pooling.dispatch import BatteryParams, DispatchResult, dispatch

HOURS_PER_YEAR = 8760.0
log = logging.getLogger(__name__)

_W: dict = {}   # per-process copy of the profiles, set by the pool initialiser


def _init(pv: np.ndarray, wind: np.ndarray, price: np.ndarray, year_id: np.ndarray, tech: BessTech) -> None:
    _W.update(pv=pv, wind=wind, price=price, year_id=year_id, tech=tech)


def battery(tech: BessTech, power_mw: float, hours: float) -> BatteryParams | None:
    if power_mw <= 0 or hours <= 0:
        return None
    return BatteryParams(power_mw=power_mw, energy_mwh=power_mw * hours,
                         round_trip_efficiency=tech.round_trip_efficiency, soc_min=tech.soc_min,
                         soc_max=tech.soc_max, soc_start=tech.soc_start,
                         degradation_cost=tech.degradation_cost_pln_per_mwh)


def summarise(res: DispatchResult, pv_gen: np.ndarray, year_id: np.ndarray, degradation_cost: float) -> dict:
    """Annual averages of one dispatch (per MW of connection), plus revenue per operating year."""
    years = len(res.gen) * res.dt_hours / HOURS_PER_YEAR
    grid_c, price_c = res.curtailment_split()
    out = {
        "gen_mwh": res.gen_mwh / years,
        "pv_gen_mwh": float(pv_gen.sum()) * res.dt_hours / years,
        "export_mwh": res.export_mwh / years,
        "revenue_pln": res.revenue / years,
        "curtailed_mwh": res.curtailed_mwh / years,
        "curtailed_grid_mwh": grid_c / years,
        "curtailed_price_mwh": price_c / years,
        "discharged_mwh": res.discharged_mwh / years,
        "wear_cost_pln": degradation_cost * res.discharged_mwh / years,
    }
    hourly_rev = res.price * res.export * res.dt_hours
    for y in np.unique(year_id):
        out[f"revenue_y{y + 1}_pln"] = float(hourly_rev[year_id == y].sum())
    return out


def _run(task: tuple[float, float, float, float]) -> dict:
    pv_r, wind_r, p_r, hours = task
    pv_gen = pv_r * _W["pv"]
    gen = pv_gen + wind_r * _W["wind"]
    tech: BessTech = _W["tech"]
    bess = battery(tech, p_r, hours)
    res = dispatch(gen, _W["price"], 1.0, bess)
    row = {"pv_ratio": pv_r, "wind_ratio": wind_r, "bess_power_ratio": p_r if bess else 0.0,
           "bess_hours": hours if bess else 0.0}
    row.update(summarise(res, pv_gen, _W["year_id"], tech.degradation_cost_pln_per_mwh if bess else 0.0))
    return row


def operating_year(index: pd.DatetimeIndex) -> np.ndarray:
    """0, 1, ... for consecutive 8760-hour blocks of the window (Oct-Sep years for the default config)."""
    return (np.arange(len(index)) // int(HOURS_PER_YEAR)).astype(int)


def tasks(sizing: Sizing) -> list[tuple[float, float, float, float]]:
    out = []
    for pv_r, wind_r, (p_r, hours) in product(sizing.pv_ratios, sizing.wind_ratios, sizing.bess_options):
        if pv_r == 0 and wind_r == 0:
            continue
        out.append((float(pv_r), float(wind_r), float(p_r), float(hours)))
    return out


def sweep(economic: pd.DataFrame, sizing: Sizing, tech: BessTech, workers: int | None = None) -> pd.DataFrame:
    """Simulate every size combination; returns one row per combination (per MW of connection)."""
    todo = tasks(sizing)
    args = (economic["pv"].to_numpy(float), economic["wind"].to_numpy(float), economic["price"].to_numpy(float),
            operating_year(economic.index), tech)
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    log.info("sizing sweep: %d combinations on %d workers", len(todo), workers)
    if workers == 1:
        _init(*args)
        rows = [_run(t) for t in todo]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=args) as pool:
            rows = list(pool.map(_run, todo, chunksize=4))
    return pd.DataFrame(rows)
