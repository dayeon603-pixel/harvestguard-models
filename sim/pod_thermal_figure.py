"""Figure for the 3D thermal result: where the cold sits, and why the setpoint has a floor."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402

import pod_thermal_3d as P  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUT: Final[Path] = Path(__file__).parent / "out"
INK, MUT, PAPER, GRID = "#12201F", "#4A5F60", "#F7F5F0", "#D5DDDA"
COLD, WARM, ALARM = "#2E7D8A", "#C08820", "#B34A28"
CMAP: Final = LinearSegmentedColormap.from_list("pod", ["#1B6C79", "#68A9A2", "#E4C77A", "#C9703F"])


def main() -> None:
    data = json.loads((OUT / "pod_thermal_3d.json").read_text())
    field = np.load(OUT / "pod_thermal_field_steady.npy")
    mask = np.load(OUT / "pod_thermal_produce_mask.npy")
    grid = P.build_grid()
    crate_mean = np.array(data["steady_state"]["crate_mean_c"])

    fig = plt.figure(figsize=(15.4, 6.0))
    fig.patch.set_facecolor(PAPER)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.02, 1.0, 0.98], wspace=0.30,
                          left=0.015, right=0.955, top=0.775, bottom=0.115)

    vmin, vmax = 10.5, 13.6
    norm = Normalize(vmin, vmax)

    # --- A: the stack in 3D -------------------------------------------------------
    ax = fig.add_subplot(gs[0, 0], projection="3d")
    ax.set_facecolor(PAPER)
    for c in range(crate_mean.size):
        cells = np.argwhere(grid.crate_id == c)
        lo = cells.min(axis=0) * [grid.dx, grid.dy, grid.dz]
        hi = (cells.max(axis=0) + 1) * [grid.dx, grid.dy, grid.dz]
        col = CMAP(norm(crate_mean[c]))
        dx_, dy_, dz_ = hi - lo
        ax.bar3d(lo[0], lo[1], lo[2], dx_, dy_, dz_, color=col,
                 edgecolor="white", linewidth=0.45, shade=True, alpha=0.97)
    ax.set_xlim(0, P.LX); ax.set_ylim(0, P.LY); ax.set_zlim(0, P.LZ)
    ax.set_box_aspect((P.LX, P.LY, P.LZ))
    ax.view_init(elev=17, azim=-58)
    for a in (ax.xaxis, ax.yaxis, ax.zaxis):
        a.pane.set_facecolor(PAPER); a.pane.set_edgecolor(GRID); a.pane.set_alpha(1.0)
    ax.tick_params(colors=MUT, labelsize=7)
    ax.set_xticks([0, 0.6, 1.2]); ax.set_yticks([0, 0.4, 0.8]); ax.set_zticks([0, 0.5, 1.0])
    ax.set_xlabel("width, m", fontsize=8, color=MUT, labelpad=-4)
    ax.set_ylabel("depth, m", fontsize=8, color=MUT, labelpad=-4)
    ax.set_zlabel("height, m", fontsize=8.5, color=MUT, labelpad=-4)
    ax.set_title("36 crates, coloured by mean temperature",
                 fontsize=12, color=INK, pad=2, fontweight="bold", loc="left")
    ax.text2D(0.0, -0.055, "evaporator sits above the stack; cold falls and pools at the top level",
              transform=ax.transAxes, fontsize=8.6, color=MUT)

    # --- B: vertical slice through the field --------------------------------------
    axb = fig.add_subplot(gs[0, 1])
    axb.set_facecolor(PAPER)
    j = field.shape[1] // 2
    slab = field[:, j, :].T
    im = axb.imshow(slab, origin="lower", cmap=CMAP, norm=norm, aspect="auto",
                    extent=[0, P.LX, 0, P.LZ], interpolation="bilinear")
    prod = mask[:, j, :].T.astype(float)
    axb.contour(np.linspace(0, P.LX, prod.shape[1]), np.linspace(0, P.LZ, prod.shape[0]),
                prod, levels=[0.5], colors="white", linewidths=1.1)
    axb.set_xlabel("width, m", fontsize=9.5, color=MUT)
    axb.set_ylabel("height, m", fontsize=9.5, color=MUT)
    axb.tick_params(colors=MUT, labelsize=8.5)
    for s in axb.spines.values():
        s.set_color(GRID)
    axb.set_title("Vertical slice through the pod centre",
                  fontsize=12, color=INK, pad=20, fontweight="bold", loc="left")
    axb.text(0.0, 1.035, "white outline = crates; the gaps between them are the air channels",
             transform=axb.transAxes, fontsize=8.6, color=MUT)
    cb = fig.colorbar(im, ax=axb, pad=0.02, fraction=0.045)
    cb.set_label("temperature, °C", fontsize=9, color=MUT)
    cb.ax.tick_params(labelsize=8, colors=MUT); cb.outline.set_visible(False)

    # --- C: the setpoint floor ----------------------------------------------------
    axc = fig.add_subplot(gs[0, 2])
    axc.set_facecolor(PAPER)
    rows = data["setpoint_floor"]
    sps = [r["setpoint_c"] for r in rows]
    cold_c = [r["coldest_c"] for r in rows]
    injured = [r["crates_injured"] for r in rows]

    axc.axhspan(P.CHILLING_FLOOR_C - 3.2, P.CHILLING_FLOOR_C, color=ALARM, alpha=0.10)
    axc.axhline(P.CHILLING_FLOOR_C, color=ALARM, lw=1.4, ls="--")
    axc.plot(sps, sps, color=MUT, lw=1.6, ls=":", label="setpoint (what a lumped model sees)")
    axc.plot(sps, cold_c, color=COLD, lw=2.4, marker="o", ms=7,
             markerfacecolor=PAPER, markeredgewidth=2, label="coldest crate (what the 3D model sees)")
    for sp, cc, nj in zip(sps, cold_c, injured):
        if nj:
            axc.annotate(f"{nj} crate{'s' if nj > 1 else ''}\ninjured", (sp, cc),
                         textcoords="offset points", xytext=(-10, -22), ha="right",
                         fontsize=8.4, color=ALARM, fontweight="bold")
    axc.text(0.03, 0.055, "chilling injury below 10 °C", transform=axc.transAxes,
             fontsize=8.8, color=ALARM, fontweight="bold")
    axc.set_xlabel("setpoint, °C", fontsize=9.5, color=MUT)
    axc.set_ylabel("temperature, °C", fontsize=9.5, color=MUT)
    axc.invert_xaxis()
    axc.set_ylim(6.6, 14.4)
    axc.set_xlim(13.35, 9.65)
    axc.legend(frameon=False, fontsize=8.6, loc="upper left", labelcolor=INK)
    for s in ("top", "right"):
        axc.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        axc.spines[s].set_color(GRID)
    axc.tick_params(colors=MUT, labelsize=8.5)
    axc.grid(axis="y", color=GRID, lw=0.7); axc.set_axisbelow(True)
    axc.set_title("Why the setpoint cannot go lower", fontsize=12, color=INK, pad=8,
                  fontweight="bold", loc="left")

    ss = data["steady_state"]
    fig.suptitle("Inside the pod: a 2.5 K stratification spread sets the setpoint, not the average",
                 fontsize=15.5, color=INK, x=0.02, ha="left", y=0.965, fontweight="bold")
    fig.text(0.02, 0.895,
             f"Finite-volume transient model, {data['grid']['cells']:,} cells at "
             f"{data['grid']['dx_mm']:.0f} mm, backward Euler. Ho February ambient 28.8 °C. "
             f"At a 13 °C setpoint the coldest crate sits at {ss['coldest_crate_c']:.2f} °C — "
             f"{ss['chilling_margin_k']:.2f} K above the chilling-injury floor.",
             fontsize=10, color=MUT, ha="left")

    fig.savefig(OUT / "pod_thermal_3d.png", dpi=175, facecolor=PAPER)
    plt.close(fig)
    logger.info("wrote %s", OUT / "pod_thermal_3d.png")


if __name__ == "__main__":
    main()
