"""Design response to the 3D finding: how much field-hot produce can the pod absorb?

`pod_thermal_3d.py` established that six crates arriving at 32 degC do not reach the quality
band within 18 hours and drag their neighbours out of band with them. The bottleneck is not
compressor capacity: it is that produce conducts at roughly 0.5 W/m.K, so a crate core cannot
reach the air no matter how cold the air is.

That is a specification problem, not a bug, and it has two possible answers. This module tests
both.

**Answer 1 — a loading policy.** Cap how much field-hot produce may enter per day, and the pod
holds. This costs nothing and is enforceable in the booking app, which already gates the door.

**Answer 2 — forced-air cooling.** Draw air *through* the crates rather than around them. This
is standard practice in commercial pre-cooling and is represented here by raising the effective
produce-to-air conductance in the flow direction; it is a reduced-order stand-in for a channel
and plenum design, not a validated duct model.

The output is the number a booking system needs: crates of field-hot produce accepted per day.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

import numpy as np

import pod_thermal_3d as P

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUT_DIR: Final[Path] = Path(__file__).parent / "out"
HOURS: Final[float] = 14.0            # overnight, load in the afternoon and sell next morning
HOT_C: Final[float] = 32.0
FORCED_AIR_K_MULTIPLIER: Final[float] = 6.0


def hot_ids(n: int) -> list[int]:
    """Choose `n` crate slots for a hot load, filling the coil-facing top level first."""
    order: list[int] = []
    for iz in range(P.CRATE_NZ - 1, -1, -1):
        for iy in range(P.CRATE_NY):
            for ix in range(P.CRATE_NX):
                order.append(ix * P.CRATE_NY * P.CRATE_NZ + iy * P.CRATE_NZ + iz)
    return order[:n]


def run_case(grid: P.Grid, n_hot: int, *, forced_air: bool) -> dict[str, float]:
    t_init = np.full(grid.shape, 13.0)
    for c in hot_ids(n_hot):
        t_init[grid.crate_id == c] = HOT_C

    k_produce = P.K_PRODUCE
    if forced_air:
        # Air is channelled through the crate rather than washing past its face. Represented
        # as a raised effective conductance; a real design achieves this with a plenum and
        # slotted crates, and the multiplier here is indicative rather than measured.
        P.K_PRODUCE = k_produce * FORCED_AIR_K_MULTIPLIER
    try:
        run = P.simulate(grid, setpoint_c=13.0, ambient_c=28.8, hours=HOURS,
                         t_initial_produce_c=13.0, t_initial_air_c=13.0,
                         label=f"{n_hot} hot, forced={forced_air}", initial_field=t_init)
    finally:
        P.K_PRODUCE = k_produce

    n_crates = int(grid.crate_id.max()) + 1
    hot = np.array(hot_ids(n_hot))
    cold = np.setdiff1d(np.arange(n_crates), hot)
    reach = []
    for c in hot:
        below = np.where(run.history_crates[:, c] <= P.QUALITY_CEILING_C)[0]
        reach.append(run.history_h[below[0]] if below.size else np.nan)
    reach = np.array(reach)

    return {
        "n_hot": n_hot,
        "forced_air": forced_air,
        "hot_in_band": int((~np.isnan(reach)).sum()),
        "slowest_h": float(np.nanmax(reach)) if (~np.isnan(reach)).any() else float("nan"),
        "warmest_c": float(run.crate_mean.max()),
        "spread_k": float(run.spread_k),
        "cold_spoiled": int((run.crate_mean[cold] > P.QUALITY_CEILING_C).sum()),
        "usable": run.usable_crates,
        "chilled": run.n_chilled,
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    grid = P.build_grid()
    n_crates = int(grid.crate_id.max()) + 1

    logger.info("=" * 104)
    logger.info("LOADING POLICY — how much field-hot produce the pod can absorb overnight (%.0f h)",
                HOURS)
    logger.info("=" * 104)
    logger.info("Pod holds a cold load at 13 degC; field-hot crates enter at %.0f degC, "
                "Ho February ambient 28.8 degC.", HOT_C)
    logger.info("")
    logger.info("%-26s %9s %10s %11s %9s %10s %9s", "configuration", "hot crates",
                "in band", "slowest", "warmest", "cold lost", "usable")
    logger.info("-" * 104)

    rows = []
    for forced in (False, True):
        for n in (2, 4, 6, 9, 12):
            r = run_case(grid, n, forced_air=forced)
            rows.append(r)
            label = ("forced-air" if forced else "room cooling") + f", {n} hot"
            slow = "not reached" if np.isnan(r["slowest_h"]) else f"{r['slowest_h']:.1f} h"
            logger.info("%-26s %9d %10s %11s %8.1fC %10d %8d/%d",
                        label, n, f"{r['hot_in_band']}/{n}", slow,
                        r["warmest_c"], r["cold_spoiled"], r["usable"], n_crates)
        logger.info("-" * 104)

    def capacity(forced: bool) -> int:
        ok = [r["n_hot"] for r in rows
              if r["forced_air"] is forced
              and r["hot_in_band"] == r["n_hot"] and r["cold_spoiled"] == 0]
        return max(ok) if ok else 0

    room, fac = capacity(False), capacity(True)
    logger.info("")
    logger.info("=" * 104)
    logger.info("THE NUMBER THE BOOKING SYSTEM NEEDS")
    logger.info("=" * 104)
    logger.info("  room cooling  : at most %2d field-hot crates per overnight cycle", room)
    logger.info("  forced air    : at most %2d field-hot crates per overnight cycle", fac)
    logger.info("")
    logger.info("  Already-cool produce is unaffected: the constraint is field heat, not volume.")
    logger.info("  The pod's 40-crate rating is a holding capacity, not a pre-cooling capacity,")
    logger.info("  and the booking app must enforce the difference.")

    (OUT_DIR / "pod_loading_policy.json").write_text(json.dumps(
        {"hours": HOURS, "hot_entry_c": HOT_C, "crates_total": n_crates,
         "forced_air_k_multiplier": FORCED_AIR_K_MULTIPLIER,
         "hot_capacity_room_cooling": room, "hot_capacity_forced_air": fac,
         "cases": rows}, indent=2))
    logger.info("")
    logger.info("wrote %s", OUT_DIR / "pod_loading_policy.json")


if __name__ == "__main__":
    main()
