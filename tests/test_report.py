import dataclasses
import json

import numpy as np
import pandas as pd
import pytest

from cable_pooling import complementarity, dashboard, pipeline
from cable_pooling.config import Site, Sizing, load_config
from cable_pooling.profiles import SiteProfiles
from cable_pooling.sizing import sweep

SITE = Site("synth", "Synthetic site", 52.0, 19.0)
SIZING = Sizing(pv_ratios=(0.0, 1.0, 2.0), wind_ratios=(0.0, 1.0), bess_options=((0.0, 0.0), (0.5, 2.0)),
                connection_sizes_mw=(10.0, 50.0))


def synthetic_profiles() -> SiteProfiles:
    idx = pd.date_range("2024-01-01", "2025-12-31 23:00", freq="h", tz="UTC")
    rng = np.random.default_rng(3)
    hod = np.asarray(idx.hour)
    pv = np.clip(np.sin((hod - 5) / 14 * np.pi), 0, None) * 0.75
    wind = np.clip(0.35 + 0.25 * np.sin(np.arange(len(idx)) / 29) + rng.normal(0, 0.1, len(idx)), 0, 1)
    hourly = pd.DataFrame({"pv": pv, "wind": wind}, index=idx)
    economic = hourly.loc["2025-04-28":"2025-05-18"].copy()
    h = np.asarray(economic.index.hour)
    economic["price"] = 450 + 250 * np.cos((h - 18) / 24 * 2 * np.pi) - 520 * (economic["pv"] > 0.6)
    weather = pd.DataFrame({"ghi": pv * 900, "dhi": pv * 200, "temp_air": 10.0, "wind_10m": 3.0}, index=idx)
    return SiteProfiles(SITE, 100.0, weather, hourly, economic)


@pytest.fixture(scope="module")
def setup():
    cfg = dataclasses.replace(load_config(), sites=(SITE,), sizing=SIZING)
    prof = synthetic_profiles()
    phys = sweep(prof.economic, cfg.sizing, cfg.bess, workers=1)
    phys.insert(0, "site", SITE.key)
    return cfg, prof, {SITE.key: phys}


def test_optimum_and_sensitivity(setup):
    cfg, _, phys = setup
    econ = pipeline.evaluate_all(cfg, phys, cfg.sizing.connection_sizes_mw)
    assert len(econ) == 2 * len(phys[SITE.key])
    opt = pipeline.optimum_table(econ)
    assert len(opt) == 2 * 2                                   # connection sizes x battery options
    assert opt.groupby("grid_mw")["overall_best"].sum().eq(1).all()
    sens = pipeline.sensitivity(cfg, phys, 10.0)
    assert set(sens["case"]) == set(pipeline.SENSITIVITY_CASES)
    base = sens[sens["case"] == "base"].iloc[0]
    cheaper = sens[sens["case"] == "battery CAPEX -30 %"].iloc[0]
    assert cheaper["npv_mpln"] >= base["npv_mpln"]


def test_dashboard_payload_is_valid_json(setup, tmp_path):
    cfg, prof, phys = setup
    econ = pipeline.evaluate_all(cfg, phys, cfg.sizing.connection_sizes_mw)
    comp = pd.DataFrame([{"site": SITE.key, **complementarity.summary(prof.hourly, prof.economic)}])
    annual = complementarity.annual_cf(prof.hourly).reset_index().assign(site=SITE.key)
    opt = pipeline.optimum_table(econ)
    row = opt[(opt["grid_mw"] == 10.0) & (opt["bess_hours"] > 0)].iloc[0]
    weeks = {SITE.key: (pipeline.example_week(prof, cfg, row), row)}
    out = tmp_path / "index.html"
    dashboard.write(cfg, econ, [prof], comp, annual, weeks, 10.0, out)
    html = out.read_text(encoding="utf-8")
    raw = html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
    data = json.loads(raw)
    assert data["bess_options"] == ["no battery", "0.5 G x 2 h"]
    grid = data["results"][SITE.key]["10"]["no battery"]["npv_pln"]
    assert len(grid) == len(SIZING.wind_ratios) and len(grid[0]) == len(SIZING.pv_ratios)
    assert grid[0][0] is None                                  # empty plant is not simulated
    assert len(data["sites"][0]["week"]["t"]) >= 167
    assert "NaN" not in raw
