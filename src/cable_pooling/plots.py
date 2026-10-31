"""Static charts for the README (matplotlib, PNG)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

from cable_pooling import complementarity as comp  # noqa: E402
from cable_pooling.economics import bess_label  # noqa: E402
from cable_pooling.economics import frontier as frontier_table  # noqa: E402

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
WIND, PV, BATTERY = "#2a78d6", "#eb6834", "#1baf7a"
# battery options: no battery in muted ink, then the categorical slots in fixed order
SERIES = [MUTED, "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
BLUES = LinearSegmentedColormap.from_list("blues", ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#104281", "#0d366b"])
DIVERGING = LinearSegmentedColormap.from_list("npv", ["#b3261e", "#e34948", "#f3b3b2", "#f0efec", "#9ec5f4",
                                                      "#3987e5", "#104281"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
    "font.size": 9.5, "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 10.5, "axes.titleweight": "bold", "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False,
    "axes.spines.right": False, "legend.frameon": False, "lines.linewidth": 2.0,
})


def _save(fig, path: Path) -> None:
    fig.savefig(path, dpi=150, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)


def complementarity(profiles, names: dict, path: Path) -> None:
    """Per site: mean summer and winter day, and monthly capacity factors of PV and wind."""
    fig, axes = plt.subplots(3, len(profiles), figsize=(4.0 * len(profiles), 8.2), sharey="row")
    for j, prof in enumerate(profiles):
        day = comp.diurnal_profiles(prof.hourly)
        for i, season in enumerate(("summer", "winter")):
            ax = axes[i, j]
            d = day.loc[season]
            ax.plot(d.index, d["pv"], color=PV, label="PV (per MWp)")
            ax.plot(d.index, d["wind"], color=WIND, label="Wind (per MW)")
            ax.plot(d.index, d["pv"] + d["wind"], color=INK2, lw=1.2, label="1 MWp PV + 1 MW wind")
            ax.set_xticks([0, 6, 12, 18, 23])
            ax.set_xlim(0, 23)
            ax.set_title(f"{names[prof.site.key]}\nmean {season} day" if i == 0 else f"mean {season} day",
                         loc="left")
            if j == 0:
                ax.set_ylabel("output per unit of capacity")
            ax.set_xlabel("hour (local time)")
        m = comp.monthly_cf(prof.hourly)
        ax = axes[2, j]
        x = np.arange(1, 13)
        ax.bar(x - 0.2, m["pv"], width=0.38, color=PV, label="PV")
        ax.bar(x + 0.2, m["wind"], width=0.38, color=WIND, label="Wind")
        ax.set_xticks(x, ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
        ax.set_title("capacity factor by month", loc="left")
        ax.grid(axis="x", visible=False)
        if j == 0:
            ax.set_ylabel("capacity factor")
    # beside the title: inside the first panel the rising PV + wind curve crossed it
    fig.legend(*axes[0, 0].get_legend_handles_labels(), loc="upper right", bbox_to_anchor=(0.995, 1.0), ncol=3,
               fontsize=8.5, frameon=False)
    fig.suptitle(f"PV and wind complement each other over the day and the year (ERA5 "
                 f"{profiles[0].hourly.index.year.min()}-{profiles[0].hourly.index.year.max()})",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    _save(fig, path)


def _matrix(econ: pd.DataFrame, value: str, pv_ratios, wind_ratios) -> np.ndarray:
    piv = econ.pivot_table(index="wind_ratio", columns="pv_ratio", values=value, aggfunc="first")
    return piv.reindex(index=wind_ratios, columns=pv_ratios).to_numpy(dtype=float)


def heatmaps(econ: pd.DataFrame, cfg, names: dict, value: str, path: Path) -> None:
    """NPV (or curtailment) over PV x wind sizes, no battery, one panel per site; best NPV marked.

    Each panel has its own colour scale (NPV: symmetric around zero), since the sites differ several-fold.
    """
    pv_r, w_r = list(cfg.sizing.pv_ratios), list(cfg.sizing.wind_ratios)
    sites = [s.key for s in cfg.sites]
    g = float(econ["grid_mw"].iloc[0])
    fig, axes = plt.subplots(1, len(sites), figsize=(4.6 * len(sites), 4.1), sharey=True)
    scale = 1e6 if value == "npv_pln" else 1.0
    for ax, k in zip(axes, sites):
        data = econ[(econ["site"] == k) & (econ["bess_hours"] == 0)]
        m = _matrix(data, value, pv_r, w_r) / scale
        if value == "npv_pln":
            lim = np.nanmax(np.abs(m))
            norm, cmap, label = TwoSlopeNorm(0.0, -lim, lim), DIVERGING, "NPV [m PLN]"
        else:
            norm, cmap, label = plt.Normalize(0, np.nanmax(m)), BLUES, "curtailed [% of generation]"
        im = ax.imshow(m, origin="lower", cmap=cmap, norm=norm, aspect="auto")
        ax.set_xticks(range(len(pv_r)), [f"{x * g:g}" for x in pv_r], rotation=90)
        ax.set_yticks(range(len(w_r)), [f"{x * g:g}" for x in w_r])
        ax.grid(False)
        ax.set_xlabel("PV [MWp]")
        ax.set_title(names[k], loc="left")
        npv = _matrix(data, "npv_pln", pv_r, w_r)
        i, j = np.unravel_index(np.nanargmax(npv), npv.shape)
        ax.scatter([j], [i], marker="*", s=220, facecolors=INK, edgecolors=SURFACE, linewidths=1.5, zorder=3)
        # installed PV MWp + wind MW equal to the connection capacity
        ax.contour(_matrix(data, "oversizing", pv_r, w_r), levels=[1.0], colors=[INK2], linewidths=1.0)
        cb = fig.colorbar(im, ax=ax, shrink=0.9, pad=0.02)
        cb.set_label(label, color=INK2)
        cb.outline.set_visible(False)
    axes[0].set_ylabel("Wind [MW]")
    title = "NPV" if value == "npv_pln" else "Share of energy curtailed"
    fig.suptitle(f"{title} of each PV x wind size, {g:g} MW connection, no battery "
                 f"(star = best NPV, line = installed capacity equal to the connection)", x=0.01, ha="left",
                 fontsize=11.5, fontweight="bold", color=INK)
    fig.tight_layout()
    _save(fig, path)


def frontier(econ: pd.DataFrame, names: dict, path: Path) -> None:
    """Best NPV for each oversizing level and battery option, per site."""
    sites = list(dict.fromkeys(econ["site"]))
    g = float(econ["grid_mw"].iloc[0])
    fig, axes = plt.subplots(2, len(sites), figsize=(4.3 * len(sites), 6.6), sharex=True, sharey="row")
    options = econ[["bess_power_ratio", "bess_hours"]].drop_duplicates().sort_values(["bess_hours", "bess_power_ratio"])
    for j, k in enumerate(sites):
        f = frontier_table(econ[econ["site"] == k])
        for c, (_, (p, h)) in zip(SERIES, options.iterrows()):
            sub = f[(f["bess_power_ratio"] == p) & (f["bess_hours"] == h)]
            lw = 2.4 if h == 0 else 1.6
            axes[0, j].plot(sub["oversizing"], sub["npv_pln"] / 1e6, color=c, lw=lw, label=bess_label(p, h))
            axes[1, j].plot(sub["oversizing"], sub["curtailed_pct"], color=c, lw=lw)
        b = f.loc[f["npv_pln"].idxmax()]
        axes[0, j].scatter([b["oversizing"]], [b["npv_pln"] / 1e6], s=60, color=INK, zorder=3)
        axes[0, j].axhline(0, color=AXIS, lw=1)
        axes[0, j].set_title(f"{names[k]}\nbest: {b['pv_mwp']:g} MWp PV + {b['wind_mw']:g} MW wind, "
                             f"battery {bess_label(b['bess_power_ratio'], b['bess_hours'])}", loc="left", fontsize=9.5)
        axes[1, j].set_xlabel("installed PV MWp + wind MW per MW of connection")
    axes[0, 0].set_ylabel(f"best NPV [m PLN], {g:g} MW connection")
    axes[1, 0].set_ylabel("curtailed [% of generation]\nat the best size")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.suptitle("How far to oversize: best NPV and curtailment at each oversizing level", x=0.01, ha="left",
                 fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), fontsize=8.5, title="battery",
               title_fontsize=8.5, bbox_to_anchor=(0.5, 0.0))
    _save(fig, path)


def week(df: pd.DataFrame, row: pd.Series, name: str, path: Path) -> None:
    """Generation, export, battery and price over one week (per MW of connection)."""
    fig, axes = plt.subplots(3, 1, figsize=(11, 7.2), sharex=True, gridspec_kw={"height_ratios": [2.2, 1, 1.2]})
    t = df.index
    ax = axes[0]
    ax.fill_between(t, 0, df["wind"], color=WIND, alpha=0.85, lw=0, label="wind available", step="post")
    ax.fill_between(t, df["wind"], df["wind"] + df["pv"], color=PV, alpha=0.85, lw=0, label="PV available",
                    step="post")
    ax.step(t, df["export"], where="post", color=INK, lw=1.4, label="exported")
    ax.axhline(1.0, color=INK2, lw=1, label="connection limit")
    ax.set_ylabel("MW per MW of connection")
    ax.legend(ncol=4, fontsize=8, loc="upper left")
    ax.set_title(f"{name}: {row['pv_ratio']:g} MWp PV + {row['wind_ratio']:g} MW wind + battery "
                 f"{bess_label(row['bess_power_ratio'], row['bess_hours'])} per MW of connection", loc="left")
    ax = axes[1]
    ax.fill_between(t, 0, df["soc"], color=BATTERY, alpha=0.8, lw=0, step="post")
    ax.set_ylabel("battery [MWh]")
    ax = axes[2]
    ax.step(t, df["price"], where="post", color=INK2, lw=1.4)
    ax.axhline(0, color=AXIS, lw=1)
    ax.set_ylabel("RCE [PLN/MWh]")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%a %d %b", tz=t.tz))
    fig.suptitle(f"Dispatch in the week with most negative prices ({t[0]:%d %b %Y})", x=0.01, ha="left",
                 fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    _save(fig, path)
