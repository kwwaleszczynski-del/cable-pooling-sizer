"""Command line interface.

Usage:
    python -m cable_pooling fetch                 # ERA5 (Open-Meteo), RCE (PSE), PVGIS check data -> data/
    python -m cable_pooling simulate --workers 8  # sizing grid for every site -> results/physics_*.csv
    python -m cable_pooling report                # economics, tables, charts, docs/index.html
    python -m cable_pooling run                   # all three
    python -m cable_pooling report --config my.toml   # other cost assumptions, no re-simulation needed
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from cable_pooling import pipeline
from cable_pooling.config import DEFAULT_CONFIG, load_config

log = logging.getLogger("cable_pooling")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cable_pooling", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in [("fetch", "download and cache open data"), ("simulate", "run the sizing grid"),
                        ("report", "economics, charts and dashboard"), ("run", "fetch + simulate + report")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="TOML file with assumptions")
        p.add_argument("--data-dir", type=Path, default=Path("data"), help="cache for downloaded data")
        p.add_argument("--results-dir", type=Path, default=Path("results"))
        p.add_argument("--docs-dir", type=Path, default=Path("docs"))
        p.add_argument("--workers", type=int, default=None, help="processes for the sizing grid")
        p.add_argument("--refresh", action="store_true", help="ignore cached downloads")
        p.add_argument("--max-weight", type=float, default=1000.0,
                       help="refuse Open-Meteo requests above this weight per 24 h (default 1000)")
        p.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    t0 = time.time()
    if args.command in ("fetch", "run"):
        pipeline.fetch(cfg, args.data_dir, refresh=args.refresh, max_weight=args.max_weight)
    if args.command in ("simulate", "run"):
        pipeline.simulate(cfg, args.data_dir, args.results_dir, workers=args.workers)
    if args.command in ("report", "run"):
        summary = pipeline.report(cfg, args.data_dir, args.results_dir, args.docs_dir)
        for b in summary["best"]:
            log.info("%s: %.1f MWp PV + %.1f MW wind, %s, curtailed %.1f %%, NPV %.1f m PLN, LCOE %.0f PLN/MWh",
                     b["site"], b["pv_mwp"], b["wind_mw"], b["battery"], b["curtailed_pct"], b["npv_pln"] / 1e6,
                     b["lcoe_pln_mwh"])
    log.info("done in %.0f s", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
