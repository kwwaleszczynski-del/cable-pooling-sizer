import numpy as np
import pandas as pd
import pytest

from cable_pooling import complementarity as comp
from cable_pooling.config import BessTech, Sizing, load_config
from cable_pooling.sizing import operating_year, sweep, tasks


def economic_frame(n_days=14, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-05-01", periods=24 * n_days, freq="h", tz="UTC")
    hod = np.asarray(idx.hour)
    pv = np.clip(np.sin((hod - 4) / 14 * np.pi), 0, None) * 0.8
    wind = np.clip(0.4 + 0.2 * np.sin(np.arange(len(idx)) / 11) + rng.normal(0, 0.1, len(idx)), 0, 1)
    price = 450 + 200 * np.cos((hod - 18) / 24 * 2 * np.pi) - 500 * (pv > 0.7)
    return pd.DataFrame({"pv": pv, "wind": wind, "price": price}, index=idx)


SIZING = Sizing(pv_ratios=(0.0, 1.0, 2.0), wind_ratios=(0.0, 1.0), bess_options=((0.0, 0.0), (0.5, 2.0)))


def test_tasks_skip_empty_plant():
    t = tasks(SIZING)
    assert len(t) == 3 * 2 * 2 - 2
    assert all(pv + w > 0 for pv, w, _, _ in t)


def test_operating_year_blocks():
    idx = pd.date_range("2024-10-01", periods=17520, freq="h", tz="UTC")
    y = operating_year(idx)
    assert y[0] == 0 and y[8759] == 0 and y[8760] == 1 and y[-1] == 1


@pytest.fixture(scope="module")
def phys():
    return sweep(economic_frame(), SIZING, BessTech(), workers=1)


def test_sweep_physics_consistent(phys):
    years = 24 * 14 / 8760
    assert (phys["export_mwh"] <= 8760 + 1e-6).all()           # never above 1 MW on average
    assert (phys["export_mwh"] * years <= 24 * 14 + 1e-6).all()
    lhs = phys["export_mwh"] + phys["curtailed_mwh"]
    # without a battery generation = export + curtailment; a battery only adds losses
    no_b = phys["bess_hours"] == 0
    np.testing.assert_allclose(lhs[no_b], phys.loc[no_b, "gen_mwh"], rtol=1e-9)
    assert (lhs[~no_b] <= phys.loc[~no_b, "gen_mwh"] + 1e-6).all()
    np.testing.assert_allclose(phys["curtailed_grid_mwh"] + phys["curtailed_price_mwh"], phys["curtailed_mwh"])


def test_battery_adds_value(phys):
    keys = ["pv_ratio", "wind_ratio"]
    a = phys[phys["bess_hours"] == 0].set_index(keys)
    b = phys[phys["bess_hours"] > 0].set_index(keys)
    net_b = b["revenue_pln"] - b["wear_cost_pln"]
    assert (net_b >= a.loc[b.index, "revenue_pln"] - 1e-6).all()


def test_complementarity_on_synthetic():
    idx = pd.date_range("2024-01-01", "2025-12-31 23:00", freq="h", tz="UTC")
    hod = np.asarray(idx.hour)
    pv = np.clip(np.sin((hod - 5) / 14 * np.pi), 0, None)
    wind = 1.0 - pv * 0.8
    hourly = pd.DataFrame({"pv": pv, "wind": wind}, index=idx)
    economic = hourly.assign(price=100.0)
    s = comp.summary(hourly, economic)
    assert s["corr_hourly"] < -0.99
    assert s["pv_capture_rate"] == pytest.approx(1.0)       # flat price -> capture rate 1
    prof = comp.diurnal_profiles(hourly)
    assert set(prof.index.get_level_values("season")) == {"winter", "spring", "summer", "autumn"}
    assert len(comp.annual_cf(hourly)) == 2


def test_default_config_loads():
    cfg = load_config()
    assert {s.key for s in cfg.sites} == {"north", "central", "southeast"}
    assert cfg.economics_start < cfg.economics_end
    assert (0.0, 0.0) in cfg.sizing.bess_options


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[period]\nweather_start="2020-01-01"\nweather_end="2021-01-01"\neconomics_start="2020-01-01"\n'
                 'economics_end="2020-12-31"\n[[sites]]\nkey="a"\nname="A"\nlatitude=50.0\nlongitude=20.0\n'
                 '[pv]\ntilt=30\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keys"):
        load_config(p)
