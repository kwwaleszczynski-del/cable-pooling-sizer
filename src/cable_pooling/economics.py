"""NPV and LCOE of each simulated configuration for a given connection size (real PLN, pre-tax).

Year y = 1..N repeats the average year of the economic window (flat real prices), with PV degradation applied to
the PV share of the plant's energy and revenue:

    f_y   = 1 - d_pv * (y - 1) * pv_share
    NPV   = -CAPEX + sum_y (f_y * (R - W) - O) / (1 + r)^y - X / (1 + r)^Y_rep
    LCOE  = (CAPEX + sum_y (O + f_y * W) / (1 + r)^y + X / (1 + r)^Y_rep) / sum_y f_y * E / (1 + r)^y

R = market revenue, W = battery wear cost, O = fixed OPEX, E = exported energy, X = battery replacement in year
Y_rep. Approximations: degradation scales energy linearly (clipping and dispatch are not re-optimised).

Usage:
    econ = evaluate(phys, grid_mw=25.0, costs=cfg.costs, finance=cfg.finance, pv=cfg.pv)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cable_pooling.config import Costs, Finance, PVParams


def discount_factors(rate: float, years: int) -> np.ndarray:
    return (1.0 + rate) ** -np.arange(1, years + 1, dtype=float)


def evaluate(phys: pd.DataFrame, grid_mw: float, costs: Costs, finance: Finance, pv: PVParams) -> pd.DataFrame:
    """Add sizes (MW), CAPEX, OPEX, NPV, LCOE and KPIs for connection capacity ``grid_mw`` to the physics rows."""
    g = float(grid_mw)
    df = phys.copy()
    df["grid_mw"] = g
    df["pv_mwp"] = df["pv_ratio"] * g
    df["wind_mw"] = df["wind_ratio"] * g
    df["bess_mw"] = df["bess_power_ratio"] * g
    df["bess_mwh"] = df["bess_power_ratio"] * df["bess_hours"] * g
    df["oversizing"] = df["pv_ratio"] + df["wind_ratio"]

    bess_capex = df["bess_mwh"] * costs.bess_capex_pln_per_mwh + df["bess_mw"] * costs.bess_capex_pln_per_mw
    capex = (df["pv_mwp"] * costs.pv_capex_pln_per_mwp + df["wind_mw"] * costs.wind_capex_pln_per_mw
             + bess_capex + costs.grid_capex_fixed_pln + g * costs.grid_capex_pln_per_mw)
    opex = (df["pv_mwp"] * costs.pv_opex_pln_per_mwp_year + df["wind_mw"] * costs.wind_opex_pln_per_mw_year
            + bess_capex * costs.bess_opex_share_of_capex + costs.grid_opex_pln_year)
    replacement = bess_capex * costs.bess_replacement_share

    revenue = df["revenue_pln"] * g
    wear = df["wear_cost_pln"] * g
    energy = df["export_mwh"] * g
    pv_share = (df["pv_gen_mwh"] / df["gen_mwh"]).fillna(0.0)

    n = finance.lifetime_years
    disc = discount_factors(finance.discount_rate, n)
    years = np.arange(n, dtype=float)
    # sum_y disc_y * f_y = sum disc - d * pv_share * sum disc_y * (y - 1)
    f_sum = disc.sum() - pv.degradation_per_year * pv_share * float((disc * years).sum())
    rep_year = costs.bess_replacement_year
    rep_disc = (1.0 + finance.discount_rate) ** -rep_year if rep_year <= n else 0.0

    df["capex_pln"] = capex
    df["opex_pln_year"] = opex
    df["npv_pln"] = -capex + f_sum * (revenue - wear) - disc.sum() * opex - rep_disc * replacement
    df["npv_pln_per_mw_grid"] = df["npv_pln"] / g
    cost_pv = capex + disc.sum() * opex + f_sum * wear + rep_disc * replacement
    df["lcoe_pln_mwh"] = cost_pv / (f_sum * energy)
    df["curtailed_pct"] = 100.0 * df["curtailed_mwh"] / df["gen_mwh"]
    df["curtailed_grid_pct"] = 100.0 * df["curtailed_grid_mwh"] / df["gen_mwh"]
    df["curtailed_price_pct"] = 100.0 * df["curtailed_price_mwh"] / df["gen_mwh"]
    df["grid_utilisation_pct"] = 100.0 * df["export_mwh"] / 8760.0
    df["capture_price_pln_mwh"] = df["revenue_pln"] / df["export_mwh"]
    return df


def bess_label(power_ratio: float, hours: float) -> str:
    """Battery option as text, power relative to the connection capacity G."""
    return "no battery" if hours == 0 else f"{power_ratio:g} G x {hours:g} h"


def best(econ: pd.DataFrame, by: str = "npv_pln") -> pd.Series:
    """Row with the highest value of ``by``."""
    return econ.loc[econ[by].idxmax()]


def frontier(econ: pd.DataFrame) -> pd.DataFrame:
    """Best NPV for each oversizing level (PV MWp + wind MW per MW of connection) and battery option."""
    keys = ["bess_power_ratio", "bess_hours", "oversizing"]
    idx = econ.groupby(keys)["npv_pln"].idxmax()
    return econ.loc[idx.to_numpy()].sort_values(keys).reset_index(drop=True)
