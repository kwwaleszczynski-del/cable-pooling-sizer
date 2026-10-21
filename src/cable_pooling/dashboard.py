"""Static interactive dashboard (docs/index.html for GitHub Pages): precomputed results embedded as JSON,
drawn with plotly.js from a CDN. Viewers pick the site, connection size, battery option and metric.

Usage:
    write(cfg, econ, profiles, comp, annual, weeks, 25.0, Path("docs/index.html"))
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from cable_pooling import complementarity
from cable_pooling.economics import bess_label

PLOTLY_JS = "https://cdn.plot.ly/plotly-2.35.2.min.js"
METRICS = {
    "npv_pln": ("NPV", "m PLN", 1e-6, 2),
    "lcoe_pln_mwh": ("LCOE", "PLN/MWh", 1.0, 0),
    "curtailed_pct": ("Energy curtailed", "% of generation", 1.0, 1),
    "grid_utilisation_pct": ("Connection utilisation", "% of 8760 h at full power", 1.0, 1),
    "capture_price_pln_mwh": ("Capture price", "PLN/MWh", 1.0, 0),
}


def _r(values, digits: int) -> list:
    return [None if not np.isfinite(v) else round(float(v), digits) for v in values]


def _matrices(econ: pd.DataFrame, pv_r, w_r) -> dict:
    out = {}
    for metric, (_, _, scale, digits) in METRICS.items():
        piv = econ.pivot_table(index="wind_ratio", columns="pv_ratio", values=metric, aggfunc="first")
        piv = piv.reindex(index=w_r, columns=pv_r) * scale
        out[metric] = [_r(row, digits) for row in piv.to_numpy(dtype=float)]
    return out


def payload(cfg, econ: pd.DataFrame, profiles, comp: pd.DataFrame, annual: pd.DataFrame, weeks: dict,
            report_grid: float) -> dict:
    pv_r, w_r = list(cfg.sizing.pv_ratios), list(cfg.sizing.wind_ratios)
    options = [list(o) for o in cfg.sizing.bess_options]
    results = {}
    for (site, g, p, h), grp in econ.groupby(["site", "grid_mw", "bess_power_ratio", "bess_hours"]):
        results.setdefault(site, {}).setdefault(f"{g:g}", {})[bess_label(p, h)] = _matrices(grp, pv_r, w_r)
    sites = []
    for prof in profiles:
        k = prof.site.key
        day = complementarity.diurnal_profiles(prof.hourly)
        month = complementarity.monthly_cf(prof.hourly)
        stats = comp[comp["site"] == k].iloc[0].drop("site").to_dict()
        a = annual[annual["site"] == k]
        wk, row = weeks[k]
        sites.append({
            "key": k, "name": prof.site.name, "lat": prof.site.latitude, "lon": prof.site.longitude,
            "stats": {kk: round(float(v), 4) for kk, v in stats.items()},
            "diurnal": {s: {"pv": _r(day.loc[s]["pv"], 4), "wind": _r(day.loc[s]["wind"], 4)}
                        for s in ("winter", "spring", "summer", "autumn")},
            "monthly": {"pv": _r(month["pv"], 4), "wind": _r(month["wind"], 4)},
            "annual": {"year": a["year"].astype(int).tolist(), "pv": _r(a["pv"], 4), "wind": _r(a["wind"], 4)},
            "week": {"t": [t.strftime("%Y-%m-%dT%H:%M") for t in wk.index],
                     **{c: _r(wk[c], 3) for c in ("pv", "wind", "export", "curtail", "soc", "price")},
                     "label": f"{row['pv_ratio']:g} MWp PV + {row['wind_ratio']:g} MW wind + battery "
                              f"{bess_label(row['bess_power_ratio'], row['bess_hours'])} per MW of connection"},
        })
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "weather_period": [str(cfg.weather_start), str(cfg.weather_end)],
        "economic_window": [str(cfg.economics_start), str(cfg.economics_end)],
        "report_grid": report_grid, "pv_ratios": pv_r, "wind_ratios": w_r, "grids": [float(g) for g in cfg.sizing.connection_sizes_mw],
        "bess_options": [bess_label(p, h) for p, h in options],
        "metrics": {k: {"label": v[0], "unit": v[1]} for k, v in METRICS.items()},
        "assumptions": {"discount_rate": cfg.finance.discount_rate, "lifetime_years": cfg.finance.lifetime_years,
                        "pv_capex": cfg.costs.pv_capex_pln_per_mwp, "wind_capex": cfg.costs.wind_capex_pln_per_mw,
                        "bess_capex_mwh": cfg.costs.bess_capex_pln_per_mwh},
        "sites": sites, "results": results,
    }


def write(cfg, econ, profiles, comp, annual, weeks, report_grid: float, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload(cfg, econ, profiles, comp, annual, weeks, report_grid), separators=(",", ":"), allow_nan=False)
    html = TEMPLATE.replace("__PLOTLY__", PLOTLY_JS).replace("__DATA__", data.replace("</", "<\\/"))
    path.write_text(html, encoding="utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cable pooling sizer</title>
<meta name="description" content="How much PV, wind and battery storage to put behind one grid connection in Poland: NPV, LCOE and curtailment on open ERA5 weather and PSE RCE prices.">
<script src="__PLOTLY__"></script>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --wind: #2a78d6; --pv: #eb6834; --battery: #1baf7a; --price: #52514e;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
    --wind: #3987e5; --pv: #d95926; --battery: #199e70; --price: #c3c2b7;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
  --wind: #3987e5; --pv: #d95926; --battery: #199e70; --price: #c3c2b7;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 32px 0 8px; }
p.lead { color: var(--ink2); margin: 0 0 16px; max-width: 820px; }
.note { color: var(--ink2); font-size: 13px; }
.controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; padding: 12px; background: var(--surface);
  border: 1px solid var(--ring); border-radius: 8px; position: sticky; top: 0; z-index: 5; }
.controls label { display: flex; flex-direction: column; font-size: 12px; color: var(--ink2); gap: 4px; }
select { font: inherit; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--axis); background: var(--surface); color: var(--ink); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 16px 0; }
.tile { background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; padding: 12px; }
.tile .k { font-size: 12px; color: var(--ink2); }
.tile .v { font-size: 22px; font-weight: 600; }
.tile .s { font-size: 12px; color: var(--muted); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.card { background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; padding: 8px; min-width: 0; }
.chart { width: 100%; height: 420px; }
.chart.small { height: 320px; }
table { border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; width: 100%; }
th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--grid); }
th:first-child, td:first-child { text-align: left; }
.disclaimer { margin-top: 32px; padding: 12px; border-left: 3px solid var(--axis); color: var(--ink2); font-size: 13px; }
a { color: var(--wind); }
@media (max-width: 820px) { .grid2 { grid-template-columns: 1fr; } .chart { height: 360px; } }
</style>
</head>
<body>
<main>
<h1>Cable pooling sizer</h1>
<p class="lead">How much PV, wind and battery storage to put behind one grid connection in Poland. Every cell is a
two-year hourly simulation (perfect-foresight dispatch on PSE RCE prices, ERA5 weather), valued with the
illustrative cost assumptions listed at the bottom. Not investment advice.</p>

<div class="controls">
  <label>Location <select id="site"></select></label>
  <label>Grid connection <select id="grid"></select></label>
  <label>Battery <select id="bess"></select></label>
  <label>Heatmap metric <select id="metric"></select></label>
</div>

<div class="tiles" id="tiles"></div>

<div class="grid2">
  <div class="card"><div id="heat" class="chart"></div></div>
  <div class="card"><div id="frontier" class="chart"></div></div>
</div>
<p class="note">Heatmap: rows = wind MW, columns = PV MWp; the star marks the highest NPV for the selected battery option.
Right: the best NPV reachable at each oversizing level (PV MWp + wind MW per MW of connection) for every battery option.</p>

<h2>Complementarity of PV and wind</h2>
<div class="grid2">
  <div class="card"><div id="diurnal" class="chart small"></div></div>
  <div class="card"><div id="monthly" class="chart small"></div></div>
</div>
<p class="note" id="compnote"></p>

<h2>Dispatch in the week with most negative prices</h2>
<div class="card"><div id="week" class="chart"></div></div>
<p class="note" id="weeknote"></p>

<h2>Best configuration per battery option</h2>
<div class="card"><table id="table"></table></div>

<div class="disclaimer" id="disc"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (v, d) => v === null || v === undefined ? "n/a" : v.toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const SERIES = ["#898781", "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"];

function fill(sel, items, labels) {
  sel.innerHTML = items.map((v, i) => `<option value="${v}">${labels ? labels[i] : v}</option>`).join("");
}
fill($("site"), D.sites.map(s => s.key), D.sites.map(s => s.name));
fill($("grid"), D.grids.map(g => String(g)), D.grids.map(g => g + " MW"));
$("grid").value = String(D.grids[Math.floor(D.grids.length / 2)]);
fill($("bess"), D.bess_options);
fill($("metric"), Object.keys(D.metrics), Object.values(D.metrics).map(m => m.label));

function layout(extra) {
  return Object.assign({
    paper_bgcolor: css("--surface"), plot_bgcolor: css("--surface"),
    font: {family: "system-ui, -apple-system, Segoe UI, sans-serif", color: css("--ink2"), size: 12},
    margin: {l: 56, r: 16, t: 40, b: 48}, hoverlabel: {font: {size: 12}},
    xaxis: {gridcolor: css("--grid"), linecolor: css("--axis"), zeroline: false},
    yaxis: {gridcolor: css("--grid"), linecolor: css("--axis"), zeroline: false},
    legend: {orientation: "h", y: -0.2},
  }, extra);
}
const CONF = {displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"]};

function current() {
  const site = D.sites.find(s => s.key === $("site").value);
  const g = Number($("grid").value);
  const res = D.results[site.key][String(g)];
  return {site, g, res, bess: $("bess").value, metric: $("metric").value};
}

function argmax(m) {
  let best = {v: -Infinity, i: 0, j: 0};
  m.forEach((row, i) => row.forEach((v, j) => { if (v !== null && v > best.v) best = {v, i, j}; }));
  return best;
}

function drawHeat(c) {
  const m = c.res[c.bess][c.metric];
  const meta = D.metrics[c.metric];
  const x = D.pv_ratios.map(r => r * c.g), y = D.wind_ratios.map(r => r * c.g);
  const npv = c.res[c.bess].npv_pln, b = argmax(npv);
  const diverging = c.metric === "npv_pln";
  const flat = m.flat().filter(v => v !== null);
  const lim = Math.max(...flat.map(Math.abs));
  const scale = diverging
    ? [[0, "#b3261e"], [0.25, "#e34948"], [0.45, "#f3b3b2"], [0.5, css("--grid")], [0.55, "#9ec5f4"], [0.75, "#3987e5"], [1, "#104281"]]
    : [[0, "#cde2fb"], [0.35, "#86b6ef"], [0.65, "#3987e5"], [1, "#0d366b"]];
  const heat = {type: "heatmap", x, y, z: m, colorscale: scale, zmin: diverging ? -lim : Math.min(...flat),
    zmax: diverging ? lim : Math.max(...flat), xgap: 2, ygap: 2,
    colorbar: {title: {text: meta.unit, side: "right"}, outlinewidth: 0, thickness: 12},
    hovertemplate: `PV %{x} MWp, wind %{y} MW<br>${meta.label}: %{z}<extra></extra>`};
  const star = {type: "scatter", mode: "markers", x: [x[b.j]], y: [y[b.i]], hoverinfo: "skip", showlegend: false,
    marker: {symbol: "star", size: 16, color: css("--ink"), line: {color: css("--surface"), width: 2}}};
  Plotly.react("heat", [heat, star], layout({
    title: {text: `${meta.label} [${meta.unit}], ${c.bess}`, x: 0.02, font: {size: 14, color: css("--ink")}},
    xaxis: {title: "PV [MWp]", type: "category", linecolor: css("--axis")},
    yaxis: {title: "Wind [MW]", type: "category", linecolor: css("--axis")},
  }), CONF);
}

function drawFrontier(c) {
  const traces = D.bess_options.map((opt, k) => {
    const npv = c.res[opt].npv_pln, curt = c.res[opt].curtailed_pct;
    const best = new Map();
    npv.forEach((row, i) => row.forEach((v, j) => {
      if (v === null) return;
      const o = Math.round((D.pv_ratios[j] + D.wind_ratios[i]) * 100) / 100;
      if (!best.has(o) || v > best.get(o).v) best.set(o, {v, pv: D.pv_ratios[j] * c.g, w: D.wind_ratios[i] * c.g, curt: curt[i][j]});
    }));
    const xs = [...best.keys()].sort((a, b) => a - b);
    return {type: "scatter", mode: "lines+markers", name: opt, x: xs, y: xs.map(o => best.get(o).v),
      customdata: xs.map(o => [best.get(o).pv, best.get(o).w, best.get(o).curt]),
      line: {width: opt === c.bess ? 3 : 1.6, color: SERIES[k % SERIES.length]}, marker: {size: opt === c.bess ? 8 : 5},
      hovertemplate: `${opt}<br>oversizing %{x}<br>NPV %{y} m PLN<br>%{customdata[0]} MWp PV + %{customdata[1]} MW wind<br>curtailed %{customdata[2]} %<extra></extra>`};
  });
  Plotly.react("frontier", traces, layout({
    title: {text: "Best NPV at each oversizing level [m PLN]", x: 0.02, font: {size: 14, color: css("--ink")}},
    xaxis: {title: "PV MWp + wind MW per MW of connection", gridcolor: css("--grid"), linecolor: css("--axis")},
    yaxis: {gridcolor: css("--grid"), linecolor: css("--axis"), zeroline: true, zerolinecolor: css("--axis")},
  }), CONF);
}

function drawTiles(c) {
  const r = c.res[c.bess], b = argmax(r.npv_pln);
  const noB = c.res["no battery"], b0 = argmax(noB.npv_pln);
  const pv = D.pv_ratios[b.j] * c.g, w = D.wind_ratios[b.i] * c.g;
  const tiles = [
    ["Best size (" + c.bess + ")", `${fmt(pv, 1)} MWp + ${fmt(w, 1)} MW`, `${fmt(D.pv_ratios[b.j] + D.wind_ratios[b.i], 2)} x the connection`],
    ["NPV", `${fmt(r.npv_pln[b.i][b.j], 1)} m PLN`, `no battery best: ${fmt(noB.npv_pln[b0.i][b0.j], 1)} m PLN`],
    ["LCOE", `${fmt(r.lcoe_pln_mwh[b.i][b.j], 0)} PLN/MWh`, `capture price ${fmt(r.capture_price_pln_mwh[b.i][b.j], 0)} PLN/MWh`],
    ["Energy curtailed", `${fmt(r.curtailed_pct[b.i][b.j], 1)} %`, "grid limit + negative prices"],
    ["Connection utilisation", `${fmt(r.grid_utilisation_pct[b.i][b.j], 1)} %`, "exported MWh / (G x 8760 h)"],
  ];
  $("tiles").innerHTML = tiles.map(t => `<div class="tile"><div class="k">${t[0]}</div><div class="v">${t[1]}</div><div class="s">${t[2]}</div></div>`).join("");
}

function drawComplementarity(c) {
  const s = c.site, h = [...Array(24).keys()];
  const tr = [];
  [["summer", "solid"], ["winter", "dot"]].forEach(([season, dash]) => {
    tr.push({type: "scatter", mode: "lines", name: `PV, ${season}`, x: h, y: s.diurnal[season].pv, line: {color: css("--pv"), width: 2, dash}});
    tr.push({type: "scatter", mode: "lines", name: `Wind, ${season}`, x: h, y: s.diurnal[season].wind, line: {color: css("--wind"), width: 2, dash}});
  });
  Plotly.react("diurnal", tr, layout({
    title: {text: "Mean day, output per unit of capacity", x: 0.02, font: {size: 14, color: css("--ink")}},
    xaxis: {title: "hour (local time)", gridcolor: css("--grid"), linecolor: css("--axis"), dtick: 3},
    hovermode: "x unified",
  }), CONF);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  Plotly.react("monthly", [
    {type: "bar", name: "PV", x: months, y: s.monthly.pv, marker: {color: css("--pv")}},
    {type: "bar", name: "Wind", x: months, y: s.monthly.wind, marker: {color: css("--wind")}},
  ], layout({title: {text: "Capacity factor by month", x: 0.02, font: {size: 14, color: css("--ink")}}, bargap: 0.3, bargroupgap: 0.08, hovermode: "x unified"}), CONF);
  const st = s.stats;
  $("compnote").textContent = `${s.name}: PV ${fmt(st.pv_yield_kwh_per_kwp, 0)} kWh/kWp per year, wind capacity factor ${fmt(100 * st.wind_cf, 1)} % (ERA5, full years ${s.annual.year[0]}-${s.annual.year[s.annual.year.length - 1]}). `
    + `Correlation PV vs wind: ${fmt(st.corr_hourly, 2)} hourly, ${fmt(st.corr_daily, 2)} daily, ${fmt(st.corr_monthly, 2)} monthly. `
    + `Capture rate (${D.economic_window[0].slice(0, 7)} to ${D.economic_window[1].slice(0, 7)}): PV ${fmt(100 * st.pv_capture_rate, 0)} %, wind ${fmt(100 * st.wind_capture_rate, 0)} % of the average RCE (${fmt(st.baseload_price, 0)} PLN/MWh).`;
}

function drawWeek(c) {
  const w = c.site.week;
  const tr = [
    {type: "scatter", mode: "lines", name: "wind available", x: w.t, y: w.wind, stackgroup: "g", line: {width: 0, shape: "hv"}, fillcolor: css("--wind")},
    {type: "scatter", mode: "lines", name: "PV available", x: w.t, y: w.pv, stackgroup: "g", line: {width: 0, shape: "hv"}, fillcolor: css("--pv")},
    {type: "scatter", mode: "lines", name: "exported", x: w.t, y: w.export, line: {color: css("--ink"), width: 1.6, shape: "hv"}},
    {type: "scatter", mode: "lines", name: "battery energy [MWh]", x: w.t, y: w.soc, yaxis: "y2", line: {color: css("--battery"), width: 2, shape: "hv"}},
    {type: "scatter", mode: "lines", name: "RCE [PLN/MWh]", x: w.t, y: w.price, yaxis: "y3", line: {color: css("--price"), width: 1.4, shape: "hv"}},
  ];
  Plotly.react("week", tr, layout({
    grid: {rows: 3, columns: 1, pattern: "independent", roworder: "top to bottom"},
    height: 560, hovermode: "x unified",
    xaxis: {gridcolor: css("--grid"), linecolor: css("--axis"), anchor: "y3"},
    yaxis: {title: "MW per MW grid", domain: [0.48, 1], gridcolor: css("--grid")},
    yaxis2: {title: "MWh", domain: [0.26, 0.42], gridcolor: css("--grid")},
    yaxis3: {title: "PLN/MWh", domain: [0, 0.2], gridcolor: css("--grid"), zeroline: true, zerolinecolor: css("--axis")},
    shapes: [{type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 1, y1: 1, line: {color: css("--ink2"), width: 1}}],
    legend: {orientation: "h", y: -0.14}, margin: {l: 56, r: 16, t: 24, b: 80},
  }), CONF);
  $("weeknote").textContent = `${c.site.name}, ${w.label} (best NPV with a battery, ${D.report_grid} MW connection). The line at 1.0 is the connection limit. Same for every connection size, since everything scales with G.`;
}

function drawTable(c) {
  const rows = D.bess_options.map(opt => {
    const r = c.res[opt], b = argmax(r.npv_pln);
    return `<tr><td>${opt}</td><td>${fmt(D.pv_ratios[b.j] * c.g, 1)}</td><td>${fmt(D.wind_ratios[b.i] * c.g, 1)}</td>`
      + `<td>${fmt(r.npv_pln[b.i][b.j], 1)}</td><td>${fmt(r.lcoe_pln_mwh[b.i][b.j], 0)}</td><td>${fmt(r.curtailed_pct[b.i][b.j], 1)}</td>`
      + `<td>${fmt(r.grid_utilisation_pct[b.i][b.j], 1)}</td><td>${fmt(r.capture_price_pln_mwh[b.i][b.j], 0)}</td></tr>`;
  });
  $("table").innerHTML = `<thead><tr><th>Battery</th><th>PV MWp</th><th>Wind MW</th><th>NPV m PLN</th><th>LCOE PLN/MWh</th>`
    + `<th>Curtailed %</th><th>Utilisation %</th><th>Capture PLN/MWh</th></tr></thead><tbody>${rows.join("")}</tbody>`;
}

function draw() {
  const c = current();
  drawTiles(c); drawHeat(c); drawFrontier(c); drawComplementarity(c); drawWeek(c); drawTable(c);
}
["site", "grid", "bess", "metric"].forEach(id => $(id).addEventListener("change", draw));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
const A = D.assumptions;
$("disc").innerHTML = `Assumptions (illustrative, real PLN, pre-tax): PV ${fmt(A.pv_capex / 1e6, 2)} m PLN/MWp, wind ${fmt(A.wind_capex / 1e6, 2)} m PLN/MW, `
  + `battery ${fmt(A.bess_capex_mwh / 1e6, 2)} m PLN/MWh + power part, discount rate ${fmt(100 * A.discount_rate, 1)} %, ${A.lifetime_years} years, prices of `
  + `${D.economic_window[0]} to ${D.economic_window[1]} repeated every year. Battery charges only from on-site generation; dispatch has perfect foresight (upper bound). `
  + `This is a portfolio model, not investment or legal advice. Data: ERA5 via Open-Meteo (CC BY 4.0), RCE prices from PSE S.A. (raporty.pse.pl). `
  + `Code and full assumptions: <a href="https://github.com/kwwaleszczynski-del/cable-pooling-sizer">github.com/kwwaleszczynski-del/cable-pooling-sizer</a>. Generated ${D.generated}.`;
draw();
</script>
</body>
</html>
"""
