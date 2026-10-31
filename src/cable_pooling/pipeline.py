"""The three stages of a run: fetch open data, simulate the sizing grid, report (tables, charts, dashboard).

Usage:
    fetch(cfg, Path("data"))
    simulate(cfg, Path("data"), Path("results"), workers=8)
    report(cfg, Path("data"), Path("results"), Path("docs"))
"""
from __future__ import annotations

import dataclasses
import json
import logging
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd

from cable_pooling import __version__, complementarity, dashboard, economics, plots, pvgis
from cable_pooling.config import Config
from cable_pooling.dispatch import dispatch
from cable_pooling.economics import bess_label
from cable_pooling.profiles import SiteProfiles, load_prices, site_profiles
from cable_pooling.pv import plane_of_array
from cable_pooling.sizing import battery, sweep

REPORT_GRID_MW = 25.0       # connection size used for the README tables and charts
PVGIS_YEARS = (2020, 2023)  # SARAH-3 overlap with the ERA5 period
log = logging.getLogger(__name__)


def fetch(cfg: Config, data_dir: Path, refresh: bool = False, max_weight: float = 1000.0) -> None:
    prices = load_prices(cfg, data_dir, refresh=refresh)
    log.info("RCE: %d hours, mean %.1f PLN/MWh", len(prices), prices.mean())
    for site in cfg.sites:
        prof = site_profiles(cfg, site, data_dir, prices, refresh=refresh, max_weight=max_weight)
        pvgis.fetch_monthly(site, *PVGIS_YEARS, cache_dir=data_dir / "pvgis", refresh=refresh)
        pvgis.fetch_pv_yearly(site, cfg.pv.tilt_deg, cfg.pv.azimuth_deg, *PVGIS_YEARS, cache_dir=data_dir / "pvgis",
                              refresh=refresh)
        log.info("%s: %d weather hours", site.key, len(prof.hourly))


def all_profiles(cfg: Config, data_dir: Path) -> list[SiteProfiles]:
    prices = load_prices(cfg, data_dir)
    return [site_profiles(cfg, s, data_dir, prices) for s in cfg.sites]


def simulate(cfg: Config, data_dir: Path, results_dir: Path, workers: int | None = None) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    for prof in all_profiles(cfg, data_dir):
        t0 = time.time()
        phys = sweep(prof.economic, cfg.sizing, cfg.bess, workers=workers)
        phys.insert(0, "site", prof.site.key)
        phys.to_csv(results_dir / f"physics_{prof.site.key}.csv", index=False, float_format="%.4f")
        log.info("%s: %d configurations in %.0f s", prof.site.key, len(phys), time.time() - t0)


def load_physics(cfg: Config, results_dir: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for s in cfg.sites:
        path = results_dir / f"physics_{s.key}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run `simulate` first")
        out[s.key] = pd.read_csv(path)
    return out


def evaluate_all(cfg: Config, phys: dict[str, pd.DataFrame], grids, costs=None, finance=None,
                 revenue_scale: float = 1.0) -> pd.DataFrame:
    frames = []
    for key, df in phys.items():
        df = df.copy()
        if revenue_scale != 1.0:
            df["revenue_pln"] *= revenue_scale
        for g in grids:
            frames.append(economics.evaluate(df, g, costs or cfg.costs, finance or cfg.finance, cfg.pv))
    return pd.concat(frames, ignore_index=True)


def optimum_table(econ: pd.DataFrame) -> pd.DataFrame:
    """Best configuration per site, connection size and battery option, plus the overall best per site and size."""
    rows = []
    for (site, g, p_r, hours), grp in econ.groupby(["site", "grid_mw", "bess_power_ratio", "bess_hours"]):
        rows.append(economics.best(grp))
    out = pd.DataFrame(rows).reset_index(drop=True)
    out["battery"] = [bess_label(p, h) for p, h in zip(out["bess_power_ratio"], out["bess_hours"])]
    best_npv = out.groupby(["site", "grid_mw"])["npv_pln"].transform("max")
    out["overall_best"] = out["npv_pln"] == best_npv
    return out


SENSITIVITY_CASES = {
    "base": {},
    "discount rate 5 %": {"finance": {"discount_rate": 0.05}},
    "discount rate 9 %": {"finance": {"discount_rate": 0.09}},
    "PV CAPEX -20 %": {"costs": {"pv_capex_pln_per_mwp": 0.8}},
    "wind CAPEX -20 %": {"costs": {"wind_capex_pln_per_mw": 0.8}},
    "battery CAPEX -30 %": {"costs": {"bess_capex_pln_per_mwh": 0.7, "bess_capex_pln_per_mw": 0.7}},
    "prices -20 %": {"revenue_scale": 0.8},
}


def sensitivity(cfg: Config, phys: dict[str, pd.DataFrame], grid_mw: float) -> pd.DataFrame:
    """Best configuration per site under changed assumptions (economics only; dispatch is not re-optimised)."""
    rows = []
    for case, change in SENSITIVITY_CASES.items():
        costs = dataclasses.replace(cfg.costs, **{k: getattr(cfg.costs, k) * v
                                                   for k, v in change.get("costs", {}).items()})
        finance = dataclasses.replace(cfg.finance, **change.get("finance", {}))
        econ = evaluate_all(cfg, phys, [grid_mw], costs, finance, change.get("revenue_scale", 1.0))
        for site, grp in econ.groupby("site"):
            b = economics.best(grp)
            no_b = economics.best(grp[grp["bess_hours"] == 0])
            rows.append({"case": case, "site": site, "pv_mwp": b["pv_mwp"], "wind_mw": b["wind_mw"],
                         "battery": bess_label(b["bess_power_ratio"], b["bess_hours"]),
                         "oversizing": b["oversizing"], "curtailed_pct": b["curtailed_pct"],
                         "npv_mpln": b["npv_pln"] / 1e6, "battery_npv_gain_mpln": (b["npv_pln"] - no_b["npv_pln"]) / 1e6,
                         "lcoe_pln_mwh": b["lcoe_pln_mwh"]})
    return pd.DataFrame(rows)


def example_week(prof: SiteProfiles, cfg: Config, row: pd.Series) -> pd.DataFrame:
    """Hourly dispatch of one configuration (per MW of connection) for the week with most negative prices."""
    e = prof.economic
    gen = row["pv_ratio"] * e["pv"] + row["wind_ratio"] * e["wind"]
    res = dispatch(gen.to_numpy(), e["price"].to_numpy(), 1.0,
                   battery(cfg.bess, row["bess_power_ratio"], row["bess_hours"]))
    df = pd.DataFrame({"pv": row["pv_ratio"] * e["pv"], "wind": row["wind_ratio"] * e["wind"],
                       "export": res.export, "curtail": res.curtail, "charge": res.charge,
                       "discharge": res.discharge, "soc": res.soc, "price": e["price"]}, index=e.index)
    local = df.tz_convert(complementarity.LOCAL_TZ)
    week_start = local.index.normalize() - pd.to_timedelta(local.index.dayofweek, unit="D")
    neg = (local["price"] < 0).groupby(week_start).sum()
    counts = local.groupby(week_start).size()
    neg = neg[counts >= 167]       # full weeks only (167 / 169 h in DST weeks)
    start = neg.idxmax()
    return local[(week_start == start)]


def irradiance_check(cfg: Config, profiles: list[SiteProfiles], data_dir: Path) -> pd.DataFrame:
    """ERA5 vs PVGIS-SARAH3 horizontal irradiation, and the model's plane-of-array irradiation and PV yield vs
    PVGIS's own PV model (default 14 % system loss) for the same plane, per year over PVGIS_YEARS."""
    rows = []
    for prof in profiles:
        m = pvgis.fetch_monthly(prof.site, *PVGIS_YEARS, cache_dir=data_dir / "pvgis")
        c = pvgis.compare(m, prof.weather)
        years = c["year"].nunique()
        ref = pvgis.fetch_pv_yearly(prof.site, cfg.pv.tilt_deg, cfg.pv.azimuth_deg, *PVGIS_YEARS,
                                    cache_dir=data_dir / "pvgis").set_index("year")
        poa = plane_of_array(prof.weather, prof.site.latitude, prof.site.longitude, prof.elevation_m,
                             cfg.pv)["poa_global"] / 1000.0
        model_poa = poa.groupby(poa.index.year).sum().reindex(ref.index)
        pv = prof.hourly["pv"]
        model_yield = pv.groupby(pv.index.year).sum().reindex(ref.index)
        rows.append({"site": prof.site.key, "era5_kwh_m2_year": c["era5_kwh_m2"].sum() / years,
                     "sarah3_kwh_m2_year": c["sarah3_kwh_m2"].sum() / years,
                     "ratio": c["era5_kwh_m2"].sum() / c["sarah3_kwh_m2"].sum(), "years": f"{PVGIS_YEARS[0]}-{PVGIS_YEARS[1]}",
                     "model_poa_kwh_m2_year": model_poa.mean(), "pvgis_poa_kwh_m2_year": ref["pvgis_poa_kwh_m2"].mean(),
                     "poa_ratio": model_poa.sum() / ref["pvgis_poa_kwh_m2"].sum(),
                     "model_kwh_kwp_year": model_yield.mean(), "pvgis_kwh_kwp_year": ref["pvgis_kwh_kwp"].mean(),
                     "yield_ratio": model_yield.sum() / ref["pvgis_kwh_kwp"].sum()})
    return pd.DataFrame(rows)


def report(cfg: Config, data_dir: Path, results_dir: Path, docs_dir: Path) -> dict:
    t0 = time.time()
    img = docs_dir / "img"
    img.mkdir(parents=True, exist_ok=True)
    phys = load_physics(cfg, results_dir)
    profiles = all_profiles(cfg, data_dir)
    econ = evaluate_all(cfg, phys, cfg.sizing.connection_sizes_mw)

    opt = optimum_table(econ)
    opt.to_csv(results_dir / "optimum.csv", index=False, float_format="%.4f")
    sens = sensitivity(cfg, phys, REPORT_GRID_MW)
    sens.to_csv(results_dir / "sensitivity.csv", index=False, float_format="%.3f")

    comp_rows, annual_rows, weeks = [], [], {}
    for prof in profiles:
        stats = complementarity.summary(prof.hourly, prof.economic)
        comp_rows.append({"site": prof.site.key, **stats})
        a = complementarity.annual_cf(prof.hourly).reset_index()
        a.insert(0, "site", prof.site.key)
        annual_rows.append(a)
        sel = opt[(opt["site"] == prof.site.key) & (opt["grid_mw"] == REPORT_GRID_MW) & (opt["bess_hours"] > 0)]
        weeks[prof.site.key] = (example_week(prof, cfg, economics.best(sel)), economics.best(sel))
    comp = pd.DataFrame(comp_rows)
    comp.to_csv(results_dir / "complementarity.csv", index=False, float_format="%.4f")
    annual = pd.concat(annual_rows, ignore_index=True)
    annual.to_csv(results_dir / "annual_cf.csv", index=False, float_format="%.4f")
    irr = irradiance_check(cfg, profiles, data_dir)
    irr.to_csv(results_dir / "irradiance_check.csv", index=False, float_format="%.3f")

    g_econ = econ[econ["grid_mw"] == REPORT_GRID_MW]
    names = {s.key: s.name for s in cfg.sites}
    plots.complementarity(profiles, names, img / "complementarity.png")
    plots.heatmaps(g_econ, cfg, names, "npv_pln", img / "npv_heatmap.png")
    plots.heatmaps(g_econ, cfg, names, "curtailed_pct", img / "curtailment_heatmap.png")
    plots.frontier(g_econ, names, img / "frontier.png")
    plots.week(weeks["central"][0], weeks["central"][1], names["central"], img / "dispatch_week.png")
    dashboard.write(cfg, econ, profiles, comp, annual, weeks, REPORT_GRID_MW, docs_dir / "index.html")

    summary = {
        "version": __version__, "python": platform.python_version(), "pandas": pd.__version__,
        "weather_period": [str(cfg.weather_start), str(cfg.weather_end)],
        "economic_window": [str(cfg.economics_start), str(cfg.economics_end)],
        "hours": int(len(profiles[0].economic)), "configurations_per_site": int(len(phys[cfg.sites[0].key])),
        "rce_mean_pln_mwh": float(profiles[0].economic["price"].mean()),
        "rce_negative_hours": int((profiles[0].economic["price"] < 0).sum()),
        "report_grid_mw": REPORT_GRID_MW,
        "best": opt[opt["overall_best"] & (opt["grid_mw"] == REPORT_GRID_MW)][
            ["site", "pv_mwp", "wind_mw", "battery", "oversizing", "curtailed_pct", "npv_pln", "lcoe_pln_mwh"]
        ].to_dict(orient="records"),
        "report_seconds": round(time.time() - t0, 1),
    }
    (results_dir / "run.json").write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    return summary


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    raise TypeError(type(o))
