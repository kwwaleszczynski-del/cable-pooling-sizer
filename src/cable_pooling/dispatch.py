"""Hourly dispatch of a hybrid plant (PV + wind + battery) behind one grid connection of G MW.

Without a battery the rule is closed-form: export ``min(gen, G)``, or nothing when the price is negative
(curtailing is free, exporting would cost money). With a battery the whole horizon is one linear program
(HiGHS via SciPy) with perfect foresight of prices and generation, so the result is an upper bound:

    max  sum_t p_t x_t dt  -  k sum_t d_t dt
    s.t. s_t = s_{t-1} + eta c_t dt - d_t dt / eta          battery energy balance (eta = sqrt(RTE))
         x_t + c_t - d_t <= g_t                             curtailment k_t = g_t - x_t - c_t + d_t >= 0
         0 <= x_t <= G                                      the connection limit, never exceeded
         0 <= c_t, d_t <= P,  s_min <= s_t <= s_max,  s_T >= s_0

The battery charges only from on-site generation (no grid import), so it shifts energy that would be curtailed
or sold cheaply. Simultaneous charging and discharging would only waste energy here (curtailment is free and
discharging has a cost k), so no binary variables are needed.

Usage:
    res = dispatch(gen_mw, price, grid_mw=1.0, bess=BatteryParams(power_mw=0.5, energy_mwh=1.0))
    res.revenue, res.curtailed_mwh, res.export
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.optimize import linprog


@dataclass(frozen=True)
class BatteryParams:
    power_mw: float
    energy_mwh: float
    round_trip_efficiency: float = 0.87
    soc_min: float = 0.05
    soc_max: float = 0.95
    soc_start: float = 0.5
    degradation_cost: float = 25.0     # PLN per MWh discharged

    @property
    def eta(self) -> float:
        return float(np.sqrt(self.round_trip_efficiency))


@dataclass
class DispatchResult:
    gen: np.ndarray
    export: np.ndarray
    charge: np.ndarray
    discharge: np.ndarray
    soc: np.ndarray
    curtail: np.ndarray
    price: np.ndarray
    grid_mw: float
    dt_hours: float = 1.0

    @property
    def revenue(self) -> float:
        """Market revenue of exported energy, PLN (battery wear cost not deducted)."""
        return float(np.sum(self.price * self.export) * self.dt_hours)

    @property
    def export_mwh(self) -> float:
        return float(self.export.sum() * self.dt_hours)

    @property
    def gen_mwh(self) -> float:
        return float(self.gen.sum() * self.dt_hours)

    @property
    def curtailed_mwh(self) -> float:
        return float(self.curtail.sum() * self.dt_hours)

    @property
    def discharged_mwh(self) -> float:
        return float(self.discharge.sum() * self.dt_hours)

    def curtailment_split(self) -> tuple[float, float]:
        """(grid-limited, price-driven) curtailment in MWh.

        Energy above the connection capacity counts as grid-limited; the rest of the curtailed energy (hours
        with negative prices) is price-driven.
        """
        over = np.maximum(self.gen - self.grid_mw, 0.0)
        grid = np.minimum(self.curtail, over)
        return float(grid.sum() * self.dt_hours), float((self.curtail - grid).sum() * self.dt_hours)


def dispatch_no_battery(gen, price, grid_mw: float, dt_hours: float = 1.0) -> DispatchResult:
    g = np.asarray(gen, dtype=float)
    p = np.asarray(price, dtype=float)
    export = np.where(p >= 0.0, np.minimum(g, grid_mw), 0.0)
    zeros = np.zeros_like(g)
    return DispatchResult(g, export, zeros, zeros.copy(), zeros.copy(), g - export, p, grid_mw, dt_hours)


def dispatch(gen, price, grid_mw: float, bess: BatteryParams | None = None, dt_hours: float = 1.0) -> DispatchResult:
    g = np.asarray(gen, dtype=float)
    p = np.asarray(price, dtype=float)
    if g.shape != p.shape:
        raise ValueError("gen and price must have the same length")
    if np.isnan(g).any() or np.isnan(p).any():
        raise ValueError("gen or price contain NaN")
    if bess is None or bess.power_mw <= 0 or bess.energy_mwh <= 0:
        return dispatch_no_battery(g, p, grid_mw, dt_hours)

    n = len(g)
    eta = bess.eta
    e_min, e_max = bess.energy_mwh * bess.soc_min, bess.energy_mwh * bess.soc_max
    s0 = bess.energy_mwh * bess.soc_start
    ix, ic, idd, isoc = 0, n, 2 * n, 3 * n          # variable layout: [export, charge, discharge, soc]

    cost = np.zeros(4 * n)
    cost[ix:ix + n] = -p * dt_hours
    cost[idd:idd + n] = bess.degradation_cost * dt_hours

    eye = sparse.identity(n, format="csr")
    zeros = sparse.csr_matrix((n, n))
    shift = sparse.eye(n, k=-1, format="csr")
    a_eq = sparse.hstack([zeros, -eta * dt_hours * eye, (dt_hours / eta) * eye, eye - shift], format="csr")
    b_eq = np.zeros(n)
    b_eq[0] = s0
    a_gen = sparse.hstack([eye, eye, -eye, zeros], format="csr")
    a_end = sparse.csr_matrix(([-1.0], ([0], [isoc + n - 1])), shape=(1, 4 * n))
    a_ub = sparse.vstack([a_gen, a_end], format="csr")
    b_ub = np.concatenate([g, [-s0]])
    lb = np.concatenate([np.zeros(3 * n), np.full(n, e_min)])
    ub = np.concatenate([np.full(n, grid_mw), np.full(2 * n, bess.power_mw), np.full(n, e_max)])

    res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=np.column_stack([lb, ub]),
                  method="highs")
    if res.status != 0:
        raise RuntimeError(f"LP failed: {res.message}")
    x = res.x
    export = np.clip(x[ix:ix + n], 0.0, grid_mw)
    charge = np.clip(x[ic:ic + n], 0.0, bess.power_mw)
    discharge = np.clip(x[idd:idd + n], 0.0, bess.power_mw)
    soc = x[isoc:isoc + n]
    curtail = np.maximum(g - export - charge + discharge, 0.0)
    return DispatchResult(g, export, charge, discharge, soc, curtail, p, grid_mw, dt_hours)
