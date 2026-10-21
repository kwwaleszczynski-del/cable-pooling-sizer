import dataclasses

import numpy as np
import pandas as pd
import pytest

from cable_pooling.config import Costs, Finance, PVParams
from cable_pooling.economics import best, discount_factors, evaluate, frontier


def phys_row(**kw):
    row = {"pv_ratio": 1.0, "wind_ratio": 0.5, "bess_power_ratio": 0.0, "bess_hours": 0.0,
           "gen_mwh": 2000.0, "pv_gen_mwh": 1000.0, "export_mwh": 1800.0, "revenue_pln": 600_000.0,
           "curtailed_mwh": 200.0, "curtailed_grid_mwh": 150.0, "curtailed_price_mwh": 50.0,
           "discharged_mwh": 0.0, "wear_cost_pln": 0.0}
    row.update(kw)
    return row


def test_discount_factors():
    np.testing.assert_allclose(discount_factors(0.1, 3), [1 / 1.1, 1 / 1.21, 1 / 1.331])


def test_npv_by_hand():
    costs = Costs(grid_capex_fixed_pln=1e6, grid_capex_pln_per_mw=0.0, grid_opex_pln_year=0.0)
    fin = Finance(discount_rate=0.05, lifetime_years=10)
    pv = PVParams(degradation_per_year=0.0)
    econ = evaluate(pd.DataFrame([phys_row()]), 2.0, costs, fin, pv).iloc[0]
    capex = 2 * 1.0 * costs.pv_capex_pln_per_mwp + 2 * 0.5 * costs.wind_capex_pln_per_mw + 1e6
    opex = 2 * costs.pv_opex_pln_per_mwp_year + 1 * costs.wind_opex_pln_per_mw_year
    annuity = (1 - 1.05 ** -10) / 0.05
    assert econ["capex_pln"] == pytest.approx(capex)
    assert econ["npv_pln"] == pytest.approx(-capex + annuity * (2 * 600_000.0 - opex))
    assert econ["curtailed_pct"] == pytest.approx(10.0)
    assert econ["grid_utilisation_pct"] == pytest.approx(100 * 1800 / 8760)


def test_lcoe_is_break_even_price():
    costs, fin, pv = Costs(), Finance(), PVParams()
    probe = evaluate(pd.DataFrame([phys_row()]), 10.0, costs, fin, pv).iloc[0]
    # if every exported MWh earned exactly the LCOE, the NPV would be zero
    at_lcoe = phys_row(revenue_pln=probe["lcoe_pln_mwh"] * 1800.0)
    econ = evaluate(pd.DataFrame([at_lcoe]), 10.0, costs, fin, pv).iloc[0]
    assert econ["npv_pln"] == pytest.approx(0.0, abs=1e-3 * econ["capex_pln"])


def test_battery_costs_and_replacement():
    costs = Costs()
    fin = Finance(discount_rate=0.0, lifetime_years=20)
    row = phys_row(bess_power_ratio=0.5, bess_hours=2.0, discharged_mwh=300.0, wear_cost_pln=7500.0)
    with_b = evaluate(pd.DataFrame([row]), 1.0, costs, fin, PVParams(degradation_per_year=0.0)).iloc[0]
    without = evaluate(pd.DataFrame([phys_row()]), 1.0, costs, fin, PVParams(degradation_per_year=0.0)).iloc[0]
    bess_capex = 1.0 * costs.bess_capex_pln_per_mwh + 0.5 * costs.bess_capex_pln_per_mw
    expected = -bess_capex * (1 + costs.bess_replacement_share + 20 * costs.bess_opex_share_of_capex) - 20 * 7500.0
    assert with_b["npv_pln"] - without["npv_pln"] == pytest.approx(expected)


def test_fixed_grid_cost_gives_economies_of_scale():
    phys = pd.DataFrame([phys_row()])
    small = evaluate(phys, 10.0, Costs(), Finance(), PVParams()).iloc[0]
    large = evaluate(phys, 50.0, Costs(), Finance(), PVParams()).iloc[0]
    assert large["npv_pln_per_mw_grid"] > small["npv_pln_per_mw_grid"]
    assert large["lcoe_pln_mwh"] < small["lcoe_pln_mwh"]


def test_pv_degradation_lowers_npv():
    phys = pd.DataFrame([phys_row()])
    no_deg = evaluate(phys, 1.0, Costs(), Finance(), PVParams(degradation_per_year=0.0)).iloc[0]
    deg = evaluate(phys, 1.0, Costs(), Finance(), dataclasses.replace(PVParams(), degradation_per_year=0.01)).iloc[0]
    assert deg["npv_pln"] < no_deg["npv_pln"]


def test_frontier_and_best():
    rows = [phys_row(pv_ratio=a, wind_ratio=b, revenue_pln=r)
            for a, b, r in [(1.0, 0.0, 1e6), (0.5, 0.5, 9e6), (0.0, 1.0, 5e6), (1.0, 1.0, 5e5)]]
    econ = evaluate(pd.DataFrame(rows), 1.0, Costs(), Finance(), PVParams())
    fr = frontier(econ)
    assert list(fr["oversizing"]) == [1.0, 2.0]
    assert fr.iloc[0]["pv_ratio"] == 0.5
    assert best(econ)["pv_ratio"] == 0.5
