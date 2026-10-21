"""Model configuration: sites, technology parameters, sizing grid, costs and finance, read from a TOML file.

Usage:
    from cable_pooling.config import load_config
    cfg = load_config("config/default.toml")
    cfg.sites[0].latitude, cfg.costs.pv_capex_pln_per_mwp
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.toml"


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class PVParams:
    tilt_deg: float = 30.0
    azimuth_deg: float = 180.0
    albedo: float = 0.2
    dc_ac_ratio: float = 1.2
    gamma_pdc: float = -0.0035
    dc_losses: float = 0.08
    inverter_efficiency: float = 0.98
    degradation_per_year: float = 0.004


@dataclass(frozen=True)
class WindParams:
    hub_height_m: float = 100.0
    losses: float = 0.12
    cut_out_ms: float = 25.0


@dataclass(frozen=True)
class BessTech:
    round_trip_efficiency: float = 0.87
    soc_min: float = 0.05
    soc_max: float = 0.95
    soc_start: float = 0.5
    degradation_cost_pln_per_mwh: float = 25.0


@dataclass(frozen=True)
class Sizing:
    pv_ratios: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0)
    wind_ratios: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5)
    bess_options: tuple[tuple[float, float], ...] = ((0.0, 0.0), (0.5, 2.0))
    connection_sizes_mw: tuple[float, ...] = (10.0, 25.0, 50.0)


@dataclass(frozen=True)
class Costs:
    fx_usd_pln: float = 3.76
    pv_capex_pln_per_mwp: float = 2.5e6
    pv_opex_pln_per_mwp_year: float = 45e3
    wind_capex_pln_per_mw: float = 7.5e6
    wind_opex_pln_per_mw_year: float = 250e3
    bess_capex_pln_per_mwh: float = 0.7e6
    bess_capex_pln_per_mw: float = 0.3e6
    bess_opex_share_of_capex: float = 0.015
    bess_replacement_year: int = 13
    bess_replacement_share: float = 0.5
    grid_capex_fixed_pln: float = 4.0e6
    grid_capex_pln_per_mw: float = 0.35e6
    grid_opex_pln_year: float = 150e3


@dataclass(frozen=True)
class Finance:
    discount_rate: float = 0.07
    lifetime_years: int = 25


@dataclass(frozen=True)
class Config:
    weather_start: date
    weather_end: date
    economics_start: date
    economics_end: date
    sites: tuple[Site, ...]
    pv: PVParams = field(default_factory=PVParams)
    wind: WindParams = field(default_factory=WindParams)
    bess: BessTech = field(default_factory=BessTech)
    sizing: Sizing = field(default_factory=Sizing)
    costs: Costs = field(default_factory=Costs)
    finance: Finance = field(default_factory=Finance)

    def site(self, key: str) -> Site:
        for s in self.sites:
            if s.key == key:
                return s
        raise KeyError(f"unknown site {key!r}")


def _build(cls, raw: dict | None):
    """Dataclass from a TOML table, rejecting unknown keys (catches typos in the config file)."""
    raw = dict(raw or {})
    names = {f.name for f in fields(cls)}
    unknown = set(raw) - names
    if unknown:
        raise ValueError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")
    for k, v in raw.items():
        if isinstance(v, list):
            raw[k] = tuple(tuple(x) if isinstance(x, list) else x for x in v)
    return cls(**raw)


def load_config(path: str | Path | None = None) -> Config:
    with open(path or DEFAULT_CONFIG, "rb") as f:
        raw = tomllib.load(f)
    period = raw.get("period", {})
    cfg = Config(
        weather_start=date.fromisoformat(period["weather_start"]),
        weather_end=date.fromisoformat(period["weather_end"]),
        economics_start=date.fromisoformat(period["economics_start"]),
        economics_end=date.fromisoformat(period["economics_end"]),
        sites=tuple(_build(Site, s) for s in raw["sites"]),
        pv=_build(PVParams, raw.get("pv")),
        wind=_build(WindParams, raw.get("wind")),
        bess=_build(BessTech, raw.get("bess")),
        sizing=_build(Sizing, raw.get("sizing")),
        costs=_build(Costs, raw.get("costs")),
        finance=_build(Finance, raw.get("finance")),
    )
    if not (cfg.weather_start <= cfg.economics_start <= cfg.economics_end <= cfg.weather_end):
        raise ValueError("the economic window must lie inside the weather period")
    return cfg
