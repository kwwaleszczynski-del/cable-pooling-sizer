import numpy as np
import pytest

from cable_pooling.dispatch import BatteryParams, dispatch, dispatch_no_battery

TOL = 1e-6


def synthetic(n_days=7, seed=0):
    """Solar-like midday peak plus noisy wind, and a duck-curve price with some negative middays."""
    rng = np.random.default_rng(seed)
    hours = np.arange(24 * n_days)
    hod = hours % 24
    pv = np.clip(np.sin((hod - 6) / 12 * np.pi), 0, None) * 1.4
    wind = np.clip(0.5 + 0.3 * np.sin(hours / 17.0) + rng.normal(0, 0.15, len(hours)), 0, 1)
    price = 400 + 250 * np.cos((hod - 19) / 24 * 2 * np.pi) - 350 * (pv > 1.0) + rng.normal(0, 30, len(hours))
    return pv + wind, price


@pytest.fixture(scope="module")
def solved():
    gen, price = synthetic()
    bess = BatteryParams(power_mw=0.5, energy_mwh=1.5, degradation_cost=10.0)
    return gen, price, bess, dispatch(gen, price, 1.0, bess)


def test_connection_never_exceeded(solved):
    _, _, _, res = solved
    assert res.export.max() <= 1.0 + TOL
    assert res.export.min() >= -TOL


def test_energy_balance_every_hour(solved):
    gen, _, _, res = solved
    np.testing.assert_allclose(res.export + res.curtail + res.charge - res.discharge, gen, atol=1e-6)
    assert res.curtail.min() >= 0


def test_battery_within_limits(solved):
    _, _, bess, res = solved
    e_min, e_max = bess.energy_mwh * bess.soc_min, bess.energy_mwh * bess.soc_max
    assert res.soc.min() >= e_min - TOL and res.soc.max() <= e_max + TOL
    assert res.charge.max() <= bess.power_mw + TOL and res.discharge.max() <= bess.power_mw + TOL
    assert res.soc[-1] >= bess.energy_mwh * bess.soc_start - TOL


def test_state_of_charge_dynamics(solved):
    _, _, bess, res = solved
    s_prev = np.concatenate([[bess.energy_mwh * bess.soc_start], res.soc[:-1]])
    np.testing.assert_allclose(res.soc - s_prev, bess.eta * res.charge - res.discharge / bess.eta, atol=1e-6)


def test_battery_never_worse_than_none(solved):
    gen, price, bess, res = solved
    base = dispatch_no_battery(gen, price, 1.0)
    assert res.revenue - bess.degradation_cost * res.discharged_mwh >= base.revenue - 1e-6
    assert res.curtailed_mwh <= base.curtailed_mwh + TOL


def test_no_battery_rule():
    gen = np.array([2.0, 0.5, 0.8, 1.2])
    price = np.array([10.0, -5.0, 0.0, 50.0])
    res = dispatch(gen, price, 1.0)
    np.testing.assert_allclose(res.export, [1.0, 0.0, 0.8, 1.0])
    np.testing.assert_allclose(res.curtail, [1.0, 0.5, 0.0, 0.2])
    grid, price_driven = res.curtailment_split()
    assert grid == pytest.approx(1.2) and price_driven == pytest.approx(0.5)


def test_hand_solved_shift():
    # surplus at a cheap hour is stored and sold at the expensive hour
    bess = BatteryParams(power_mw=1.0, energy_mwh=1.0, round_trip_efficiency=1.0, soc_min=0.0, soc_max=1.0,
                         soc_start=0.0, degradation_cost=0.0)
    res = dispatch(np.array([2.0, 0.0]), np.array([10.0, 100.0]), 1.0, bess)
    np.testing.assert_allclose(res.export, [1.0, 1.0], atol=1e-9)
    assert res.revenue == pytest.approx(110.0)
    assert res.curtailed_mwh == pytest.approx(0.0, abs=1e-9)


def test_no_export_at_negative_prices(solved):
    _, price, _, res = solved
    assert np.all(res.export[price < 0] <= TOL)


def test_battery_charges_only_from_site():
    # no generation at all: nothing can be bought from the grid, so the battery stays idle
    bess = BatteryParams(power_mw=1.0, energy_mwh=2.0, degradation_cost=0.0)
    res = dispatch(np.zeros(24), np.linspace(-100, 500, 24), 1.0, bess)
    assert res.charge.sum() <= TOL and res.export.sum() <= TOL


def test_input_checks():
    with pytest.raises(ValueError):
        dispatch(np.ones(3), np.ones(4), 1.0)
    with pytest.raises(ValueError):
        dispatch(np.array([1.0, np.nan]), np.ones(2), 1.0)
