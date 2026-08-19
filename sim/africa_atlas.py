"""Deployment atlas: the continental sizing result rendered as three linked figures.

Panel 1  Map      — where each SKU is required, over real Natural Earth geometry.
Panel 2  Envelope — the physical reason for the partition: solar resource against ambient
                    temperature, with the SKU boundary shown as it actually falls.
Panel 3  Curve    — how many SKUs the product line needs, and what each one costs.

The palette is instrument-panel rather than agricultural: the variable being encoded is
electrical demand, so the ramp runs cool (low draw, highland) to hot (high draw, Sahel and
humid lowland), and country geometry stays a quiet ground so the points carry the signal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

HERE: Final[Path] = Path(__file__).parent
OUT_DIR: Final[Path] = HERE / "out"
WORLD: Final[Path] = HERE / "data" / "world.geojson"

INK: Final[str] = "#12201F"
INK_2: Final[str] = "#4A5F60"
PAPER: Final[str] = "#F7F5F0"
LAND: Final[str] = "#E6E4DD"
LAND_EDGE: Final[str] = "#CFCCC2"
SEA: Final[str] = "#F2F1EC"

SKU_COLORS: Final[dict[str, str]] = {
    "HG-A": "#2E7D8A",   # highland, low draw
    "HG-B": "#C89B2C",   # mixed
    "HG-C": "#B34A28",   # humid lowland and Sahel, high draw
}
SKU_LABEL: Final[dict[str, str]] = {
    "HG-A": "HG-A · 200 W",
    "HG-B": "HG-B · 300 W",
    "HG-C": "HG-C · 440 W",
}
DEMAND_CMAP: Final = LinearSegmentedColormap.from_list(
    "demand", ["#2E7D8A", "#7FA88C", "#C89B2C", "#B34A28"]
)


def load() -> tuple[dict, dict[str, str]]:
    data = json.loads((OUT_DIR / "africa_sizing.json").read_text())
    assign = {s: k["name"] for k in data["skus"] for s in k["sites"]}
    return data, assign


def _style_axis(ax) -> None:
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(LAND_EDGE)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(color=LAND_EDGE, lw=0.7, alpha=0.8)
    ax.set_axisbelow(True)


def panel_map(ax, sites: list[dict], assign: dict[str, str]) -> None:
    world = gpd.read_file(WORLD)
    africa = world[world["CONTINENT"] == "Africa"] if "CONTINENT" in world.columns else world
    africa.plot(ax=ax, color=LAND, edgecolor=LAND_EDGE, linewidth=0.6)

    ax.set_facecolor(SEA)
    ax.set_xlim(-20, 53)
    ax.set_ylim(-36, 39)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    # Dense clusters (the Gulf of Guinea coast, the Rift) need labels pushed apart or they
    # overplot; offsets are per-site rather than uniform.
    offsets = {
        "kumasi": (-26, 4), "abidjan": (-30, -6), "cotonou": (-4, -12), "ibadan": (20, 4),
        "owerri": (24, -6), "tamale": (-24, 6), "ho": (16, 8), "yaounde": (22, -2),
        "meru": (24, 2), "nakuru": (-24, -6), "kampala": (-26, 4), "kigali": (-26, -4),
        "arusha": (22, -6), "addis": (-26, 2), "hawassa": (22, -4), "bahirdar": (22, 6),
        "ouaga": (-2, 10), "bamako": (-24, 4), "niamey": (10, 10), "kano": (18, 6),
        "jos": (18, -8), "dakar": (-24, 2), "lilongwe": (22, 4), "lusaka": (-26, -2),
        "harare": (-24, -8), "nampula": (22, -2), "polokwane": (16, -10),
        "windhoek": (-28, -2), "kinshasa": (-28, 2), "marrakech": (-6, 10),
        "hargeisa": (20, 8), "mbeya": (20, 4), "fayoum": (18, 4),
    }
    for s in sites:
        sku = assign.get(s["key"], "HG-C")
        ax.scatter(s["lon"], s["lat"], s=112, c=SKU_COLORS[sku], edgecolor=PAPER,
                   linewidth=1.4, zorder=5)
        dx, dy = offsets.get(s["key"], (0, 9))
        ax.annotate(s["name"].split(",")[0], (s["lon"], s["lat"]),
                    textcoords="offset points", xytext=(dx, dy),
                    ha="left" if dx > 4 else ("right" if dx < -4 else "center"),
                    va="center" if abs(dx) > 4 else "bottom",
                    fontsize=6.8, color=INK, zorder=6,
                    path_effects=[pe.withStroke(linewidth=2.4, foreground=PAPER)])

    ax.set_title("Which pod a region needs", fontsize=13, color=INK, loc="left", pad=12,
                 fontweight="bold")
    ax.text(0.0, -0.035,
            "33 smallholder horticulture regions, sized independently from NASA POWER climatology",
            transform=ax.transAxes, fontsize=9, color=INK_2, va="top")
    ax.legend(handles=[Line2D([], [], marker="o", ls="", markersize=9, markerfacecolor=c,
                              markeredgecolor=PAPER, label=SKU_LABEL[k])
                       for k, c in SKU_COLORS.items()],
              loc="lower left", frameon=False, fontsize=9, labelcolor=INK,
              bbox_to_anchor=(0.005, 0.005))


def panel_envelope(ax, sites: list[dict], assign: dict[str, str]) -> None:
    dm = [s["peak_demand_wh"] for s in sites]
    sc = ax.scatter([s["annual_ghi"] for s in sites], [s["mean_temp_c"] for s in sites],
                    c=dm, cmap=DEMAND_CMAP, s=[s["array_w"] * 0.62 for s in sites],
                    edgecolor=PAPER, linewidth=1.2, zorder=4)
    for s in sites:
        if s["key"] in ("ho", "meru", "niamey", "nakuru", "kano", "hargeisa", "yaounde"):
            ax.annotate(s["name"].split(",")[0], (s["annual_ghi"], s["mean_temp_c"]),
                        textcoords="offset points", xytext=(0, 13), ha="center",
                        fontsize=8, color=INK, zorder=6,
                        path_effects=[pe.withStroke(linewidth=2.4, foreground=PAPER)])
    _style_axis(ax)
    ax.set_xlabel("annual solar resource, kWh/m²/day", fontsize=10, color=INK_2)
    ax.set_ylabel("mean ambient temperature, °C", fontsize=10, color=INK_2)
    ax.set_title("Why the partition falls where it does", fontsize=13, color=INK,
                 loc="left", pad=12, fontweight="bold")
    ax.text(0.0, 1.015, "marker area = array required · colour = peak daily demand",
            transform=ax.transAxes, fontsize=9, color=INK_2)
    cb = ax.figure.colorbar(sc, ax=ax, pad=0.02, fraction=0.045)
    cb.set_label("peak demand, Wh/day", fontsize=9, color=INK_2)
    cb.ax.tick_params(labelsize=8, colors=INK_2)
    cb.outline.set_visible(False)

    ax.text(0.015, 0.955, "hot + dim\nhumid lowland, Sahel\nlargest array", transform=ax.transAxes,
            fontsize=8.5, color="#B34A28", fontweight="bold", va="top", linespacing=1.35)
    ax.text(0.985, 0.045, "bright + cool\nhighland\nsmallest array", transform=ax.transAxes,
            fontsize=8.5, color="#2E7D8A", fontweight="bold", va="bottom", ha="right",
            linespacing=1.35)


def panel_curve(ax, curve: list[dict], skus: list[dict]) -> None:
    ks = [c["k"] for c in curve]
    ws = [c["waste_usd_per_pod"] for c in curve]
    ax.plot(ks, ws, color=INK_2, lw=2, marker="o", ms=6, markerfacecolor=PAPER,
            markeredgewidth=1.8, zorder=4)
    chosen = len(skus)
    ax.scatter([chosen], [curve[chosen - 1]["waste_usd_per_pod"]], s=190, zorder=5,
               color=SKU_COLORS["HG-B"], edgecolor=PAPER, linewidth=2)
    ax.annotate(f"{chosen} SKUs\n${curve[chosen - 1]['waste_usd_per_pod']:.2f}/pod wasted",
                (chosen, curve[chosen - 1]["waste_usd_per_pod"]),
                textcoords="offset points", xytext=(14, 16), fontsize=9, color=INK,
                fontweight="bold")
    _style_axis(ax)
    ax.set_xticks(ks)
    ax.set_xlabel("number of SKUs in the product line", fontsize=10, color=INK_2)
    ax.set_ylabel("oversizing cost, $/pod", fontsize=10, color=INK_2)
    ax.set_title("How many pods to manufacture", fontsize=13, color=INK, loc="left",
                 pad=12, fontweight="bold")
    ax.text(0.0, 1.015, "exact partition of the 33 site requirements, by dynamic programming",
            transform=ax.transAxes, fontsize=9, color=INK_2)


def main() -> None:
    data, assign = load()
    sites = data["sites_warm"]

    fig = plt.figure(figsize=(16.4, 9.2))
    fig.patch.set_facecolor(PAPER)
    gs = fig.add_gridspec(2, 2, width_ratios=[1.06, 1], height_ratios=[1.32, 1],
                          hspace=0.30, wspace=0.17,
                          left=0.035, right=0.965, top=0.885, bottom=0.065)

    ax_map = fig.add_subplot(gs[:, 0])
    ax_env = fig.add_subplot(gs[0, 1])
    ax_cur = fig.add_subplot(gs[1, 1])

    panel_map(ax_map, sites, assign)
    panel_envelope(ax_env, sites, assign)
    panel_curve(ax_cur, data["sku_curve"], data["skus"])

    fig.suptitle("HarvestGuard deployment atlas — one sizing engine, three pods, a continent",
                 fontsize=18, color=INK, x=0.035, ha="left", y=0.965, fontweight="bold")
    fig.text(0.035, 0.925,
             "Every region solved independently against two hard constraints: the energy balance closes in "
             "all twelve months, and no-sun autonomy clears 48 hours — each verified under a stacked "
             "pessimistic case.",
             fontsize=10.5, color=INK_2, ha="left")

    out = OUT_DIR / "africa_deployment_atlas.png"
    fig.savefig(out, dpi=175, facecolor=PAPER)
    plt.close(fig)
    logger.info("wrote %s", out)

    arrays = sorted(s["array_w"] for s in sites)
    logger.info("array requirement: min %.0f W, median %.0f W, max %.0f W",
                arrays[0], arrays[len(arrays) // 2], arrays[-1])
    for k in data["skus"]:
        logger.info("  %-5s %4.0f W / %.1f kWh / $%.0f -> %2d regions",
                    k["name"], k["array_w"], k["battery_kwh"], k["cost_usd"], len(k["sites"]))


if __name__ == "__main__":
    main()
