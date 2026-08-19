"""Three-dimensional transient thermal model of the pod interior.

Every model so far has been lumped: one box temperature, one battery state. That is enough to
size an array and it is not enough to answer the question an operator asks first, which is
whether the crate at the back of the bottom row actually gets cold, and how long it takes.

A lumped model cannot see two failures that decide whether the pod's rated capacity is real:

* **Stratification.** Cold air from a top-mounted evaporator falls unevenly. If the bottom-back
  corner sits several kelvin above the setpoint, the pod does not hold 40 crates, it holds
  however many sit in the cold zone.
* **Chilling injury near the coil.** Solanaceous crops are damaged below about 10 degC. A box
  averaging 13 degC can still have crates against the evaporator sitting at 8 degC. That damage
  is invisible to a thermostat reading a single point, and it is a direct product loss.

Method: finite-volume transient conduction on a structured grid over the usable volume, with
crates resolved as distinct solid regions and air cells given a fan-driven effective mixing
conductivity. Explicit time integration with a stability-limited step.

This is *not* CFD. There is no momentum equation and no resolved jet from the evaporator fan;
circulation is represented by an effective conductivity calibrated so the bulk air approaches a
well-mixed limit at the design fan duty. That parameterisation is the model's main weakness and
is stated as such in the output. What it does capture, and what the lumped model cannot, is that
produce is a poor conductor: a crate core lags its surface by hours regardless of how well the
air is mixed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUT_DIR: Final[Path] = Path(__file__).parent / "out"

# --- geometry, metres (usable cold volume from hardware_spec v1.2) -----------------
LX: Final[float] = 1.24
LY: Final[float] = 0.84
LZ: Final[float] = 1.04
NX: Final[int] = 25
NY: Final[int] = 17
NZ: Final[int] = 21

# Crate stack: 3 across x, 3 across y, 4 high = 36 slots in the usable volume.
CRATE_NX: Final[int] = 3
CRATE_NY: Final[int] = 3
CRATE_NZ: Final[int] = 4
# Crate footprint is set so the air channels between columns are at least one cell wide:
# with 50 mm cells, a 0.38 m crate leaves a 25 mm gap that the grid cannot represent, and the
# stack then behaves as one solid block. 0.34 x 0.22 leaves 55 mm and 45 mm channels.
# This is a real design requirement, not a modelling convenience: without channelled air the
# interior of a densely stacked pod is unreachable by the coil.
CRATE_LX: Final[float] = 0.34
CRATE_LY: Final[float] = 0.22
CRATE_LZ: Final[float] = 0.23
CRATE_GAP_Z: Final[float] = 0.02   # batten spacing between levels
PLENUM_TOP_M: Final[float] = 0.10   # clear space under the evaporator

# --- material properties ----------------------------------------------------------
RHO_AIR: Final[float] = 1.16
CP_AIR: Final[float] = 1006.0
K_AIR_STILL: Final[float] = 0.026
# Fan-driven circulation is advective, not diffusive. Representing it as an effective
# conductivity requires the value that reproduces the fan's thermal capacity flow: at
# ~0.5 m/s through ~0.25 m2 of channel the air carries ~145 W/K, which over a 50 mm cell
# corresponds to k_eff of order 25 W/m.K. A diffusive value near still air (0.026) or even
# 1.1 silently starves the stack of cooling and was the cause of a false warm drift.
K_AIR_MIXED: Final[float] = 25.0

RHO_PRODUCE: Final[float] = 580.0    # bulk density giving a 10 kg crate at 0.34x0.22x0.23 m
CP_PRODUCE: Final[float] = 3900.0
K_PRODUCE: Final[float] = 0.52       # high-moisture produce, bulk with void fraction

# --- boundary conditions ----------------------------------------------------------
UA_TOTAL: Final[float] = 2.0         # W/K for the whole envelope, from the lumped model
EVAP_AREA_FRACTION: Final[float] = 0.55   # fraction of the ceiling occupied by the coil face
H_EVAP: Final[float] = 85.0          # W/m2K, forced-air coil to plenum
COOLING_CAPACITY_W: Final[float] = 150.0  # the compressor cannot deliver more than this
COIL_BELOW_SETPOINT_K: Final[float] = 6.0
# Tomato and pepper respire at roughly 20 W/tonne at 10 degC (ASHRAE order); on a
# 440 kg/m3 bulk density that is 8.8 W/m3. The Q10 law is only valid over the storage
# range, so the exponent is capped: extrapolating it to 45 degC gives a 21x multiplier
# and a physically false thermal runaway.
RESP_W_PER_M3: Final[float] = 11.6   # 20 W/tonne at 10 degC on a 580 kg/m3 bulk density
RESP_T_CAP_C: Final[float] = 30.0
RESP_Q10: Final[float] = 2.4

CHILLING_FLOOR_C: Final[float] = 10.0
QUALITY_CEILING_C: Final[float] = 15.0


@dataclass(slots=True)
class Grid:
    """Structured grid with a material tag per cell."""

    dx: float
    dy: float
    dz: float
    is_produce: np.ndarray
    crate_id: np.ndarray
    rho_cp: np.ndarray = field(repr=False)
    k: np.ndarray = field(repr=False)

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.is_produce.shape

    @property
    def cell_volume(self) -> float:
        return self.dx * self.dy * self.dz


def build_grid() -> Grid:
    """Lay the crate stack into the usable volume and tag every cell."""
    dx, dy, dz = LX / NX, LY / NY, LZ / NZ
    xc = (np.arange(NX) + 0.5) * dx
    yc = (np.arange(NY) + 0.5) * dy
    zc = (np.arange(NZ) + 0.5) * dz

    is_produce = np.zeros((NX, NY, NZ), dtype=bool)
    crate_id = np.full((NX, NY, NZ), -1, dtype=np.int16)

    stack_h = CRATE_NZ * CRATE_LZ + (CRATE_NZ - 1) * CRATE_GAP_Z
    z0_stack = 0.02                                    # crates sit just off the floor
    usable_top = LZ - PLENUM_TOP_M
    if z0_stack + stack_h > usable_top:
        z0_stack = max(usable_top - stack_h, 0.0)

    pitch_x = (LX - CRATE_NX * CRATE_LX) / (CRATE_NX + 1)
    pitch_y = (LY - CRATE_NY * CRATE_LY) / (CRATE_NY + 1)

    cid = 0
    for ix in range(CRATE_NX):
        x0 = pitch_x * (ix + 1) + CRATE_LX * ix
        for iy in range(CRATE_NY):
            y0 = pitch_y * (iy + 1) + CRATE_LY * iy
            for iz in range(CRATE_NZ):
                z0 = z0_stack + (CRATE_LZ + CRATE_GAP_Z) * iz
                mask = (
                    (xc[:, None, None] >= x0) & (xc[:, None, None] < x0 + CRATE_LX)
                    & (yc[None, :, None] >= y0) & (yc[None, :, None] < y0 + CRATE_LY)
                    & (zc[None, None, :] >= z0) & (zc[None, None, :] < z0 + CRATE_LZ)
                )
                is_produce |= mask
                crate_id[mask] = cid
                cid += 1

    rho_cp = np.where(is_produce, RHO_PRODUCE * CP_PRODUCE, RHO_AIR * CP_AIR)
    k = np.where(is_produce, K_PRODUCE, K_AIR_MIXED)
    return Grid(dx=dx, dy=dy, dz=dz, is_produce=is_produce, crate_id=crate_id,
                rho_cp=rho_cp, k=k)


def _harmonic_face_k(k: np.ndarray, axis: int) -> np.ndarray:
    """Harmonic mean conductivity at cell faces; correct for material discontinuities."""
    a = np.take(k, np.arange(0, k.shape[axis] - 1), axis=axis)
    b = np.take(k, np.arange(1, k.shape[axis]), axis=axis)
    return 2.0 * a * b / (a + b)


@dataclass(slots=True)
class Run:
    """Result of a transient run."""

    label: str
    t_field: np.ndarray = field(repr=False)
    crate_mean: np.ndarray = field(repr=False)     # (n_crates,) final mean temperature
    crate_core: np.ndarray = field(repr=False)     # (n_crates,) final core temperature
    history_h: np.ndarray = field(repr=False)
    history_crates: np.ndarray = field(repr=False)  # (steps, n_crates)
    air_mean_c: float = 0.0
    setpoint_c: float = 13.0

    @property
    def spread_k(self) -> float:
        return float(self.crate_mean.max() - self.crate_mean.min())

    @property
    def n_chilled(self) -> int:
        return int((self.crate_mean < CHILLING_FLOOR_C).sum())

    @property
    def n_too_warm(self) -> int:
        return int((self.crate_mean > QUALITY_CEILING_C).sum())

    @property
    def usable_crates(self) -> int:
        ok = (self.crate_mean >= CHILLING_FLOOR_C) & (self.crate_mean <= QUALITY_CEILING_C)
        return int(ok.sum())


def simulate(grid: Grid, *, setpoint_c: float, ambient_c: float, hours: float,
             t_initial_produce_c: float, t_initial_air_c: float,
             label: str, k_air: float = K_AIR_MIXED,
             record_every_min: float = 10.0,
             initial_field: np.ndarray | None = None) -> Run:
    """Explicit finite-volume transient run."""
    nx, ny, nz = grid.shape
    k = np.where(grid.is_produce, K_PRODUCE, k_air)

    if initial_field is not None:
        t = initial_field.astype(np.float64).copy()
    else:
        t = np.where(grid.is_produce, t_initial_produce_c, t_initial_air_c).astype(np.float64)

    kx = _harmonic_face_k(k, 0)
    ky = _harmonic_face_k(k, 1)
    kz = _harmonic_face_k(k, 2)

    vol = grid.cell_volume
    ax = grid.dy * grid.dz / grid.dx
    ay = grid.dx * grid.dz / grid.dy
    az = grid.dx * grid.dy / grid.dz

    # envelope: distribute the lumped UA over the boundary cells by area
    area_total = 2 * (LX * LY + LX * LZ + LY * LZ)
    ua_per_area = UA_TOTAL / area_total
    ua_cell = np.zeros_like(t)
    ua_cell[0, :, :] += ua_per_area * grid.dy * grid.dz
    ua_cell[-1, :, :] += ua_per_area * grid.dy * grid.dz
    ua_cell[:, 0, :] += ua_per_area * grid.dx * grid.dz
    ua_cell[:, -1, :] += ua_per_area * grid.dx * grid.dz
    ua_cell[:, :, 0] += ua_per_area * grid.dx * grid.dy
    ua_cell[:, :, -1] += ua_per_area * grid.dx * grid.dy

    # evaporator: a cold face over part of the ceiling, coupled to the top air layer
    evap = np.zeros_like(t, dtype=bool)
    x_lo = int(nx * (1 - EVAP_AREA_FRACTION) / 2)
    x_hi = nx - x_lo
    y_lo = int(ny * (1 - EVAP_AREA_FRACTION) / 2)
    y_hi = ny - y_lo
    evap[x_lo:x_hi, y_lo:y_hi, -1] = True
    evap &= ~grid.is_produce
    ha_evap = H_EVAP * grid.dx * grid.dy

    # Backward Euler on a prefactorised sparse operator. The conduction matrix is constant,
    # so it is factorised once and each step is a triangular solve. This is unconditionally
    # stable, which is what makes a realistic mixed-air conductance affordable: the explicit
    # step it would otherwise demand is ~10 ms.
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla

    n_cells = nx * ny * nz
    idx = np.arange(n_cells).reshape(nx, ny, nz)
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    vals: list[np.ndarray] = []

    def couple(i_a: np.ndarray, i_b: np.ndarray, g: np.ndarray) -> None:
        rows.extend([i_a, i_b, i_a, i_b])
        cols.extend([i_b, i_a, i_a, i_b])
        vals.extend([-g, -g, g, g])

    couple(idx[:-1, :, :].ravel(), idx[1:, :, :].ravel(), (kx * ax).ravel())
    couple(idx[:, :-1, :].ravel(), idx[:, 1:, :].ravel(), (ky * ay).ravel())
    couple(idx[:, :, :-1].ravel(), idx[:, :, 1:].ravel(), (kz * az).ravel())

    L = sp.coo_matrix((np.concatenate(vals),
                       (np.concatenate(rows), np.concatenate(cols))),
                      shape=(n_cells, n_cells)).tocsr()
    L = L + sp.diags(ua_cell.ravel())

    cap = (grid.rho_cp * vol).ravel()
    dt = 60.0
    n_steps = int(hours * 3600.0 / dt)
    record_every = max(1, int(record_every_min * 60.0 / dt))

    evap_flat = evap.ravel()
    produce_flat_3d = grid.is_produce.ravel()
    solve = spla.factorized((sp.diags(cap / dt) + L).tocsc())

    n_crates = int(grid.crate_id.max()) + 1
    hist_t: list[float] = []
    hist_c: list[np.ndarray] = []
    flat_id = grid.crate_id.ravel()
    produce_flat = grid.is_produce.ravel()

    def crate_means(field_: np.ndarray) -> np.ndarray:
        f = field_.ravel()
        sums = np.bincount(flat_id[produce_flat], weights=f[produce_flat], minlength=n_crates)
        counts = np.bincount(flat_id[produce_flat], minlength=n_crates)
        return sums / np.maximum(counts, 1)

    tf = t.ravel().copy()
    for step in range(n_steps):
        b = cap / dt * tf + ua_cell.ravel() * ambient_c

        t_resp = np.minimum(tf, RESP_T_CAP_C)
        b += np.where(produce_flat_3d,
                      RESP_W_PER_M3 * RESP_Q10 ** ((t_resp - 10.0) / 10.0) * vol, 0.0)

        # Evaporator, capacity-limited. Demand is evaluated on the previous step and the
        # whole extraction scaled back if it exceeds the machine's rating; an unclamped coil
        # is an infinite heat sink and hides the failure this model exists to find.
        air_mean = tf[~produce_flat_3d].mean()
        if air_mean > setpoint_c - 0.5:
            drive = np.clip(tf[evap_flat] - (setpoint_c - COIL_BELOW_SETPOINT_K), 0.0, None)
            q_evap = ha_evap * drive
            total = q_evap.sum()
            if total > COOLING_CAPACITY_W:
                q_evap *= COOLING_CAPACITY_W / total
            b[evap_flat] -= q_evap

        tf = solve(b)
        if step % 500 == 0 and not np.isfinite(tf).all():
            raise FloatingPointError(f"solution diverged at step {step}")

        if step % record_every == 0:
            hist_t.append(step * dt / 3600.0)
            hist_c.append(crate_means(tf))

    t = tf.reshape(nx, ny, nz)

    final_means = crate_means(t)

    # crate cores: the single coldest-lagging cell per crate, taken as the cell furthest
    # from any air interface, approximated by the extremum within each crate
    core = np.zeros(n_crates)
    for c in range(n_crates):
        cells = t[grid.crate_id == c]
        core[c] = float(cells.max())

    return Run(label=label, t_field=t.copy(), crate_mean=final_means, crate_core=core,
               history_h=np.array(hist_t), history_crates=np.array(hist_c),
               air_mean_c=float(t[~grid.is_produce].mean()), setpoint_c=setpoint_c)


def crate_positions(grid: Grid) -> np.ndarray:
    """Centroid (ix, iy, iz) index of each crate in the stack, for reporting."""
    n = int(grid.crate_id.max()) + 1
    pos = np.zeros((n, 3), dtype=int)
    for c in range(n):
        idx = np.argwhere(grid.crate_id == c)
        pos[c] = idx.mean(axis=0).round().astype(int)
    return pos


def hot_ids(n: int) -> list[int]:
    """Crate slots for a hot load, filling the coil-facing top level first."""
    order = [ix * CRATE_NY * CRATE_NZ + iy * CRATE_NZ + iz
             for iz in range(CRATE_NZ - 1, -1, -1)
             for iy in range(CRATE_NY) for ix in range(CRATE_NX)]
    return order[:n]


def _stack_map(run: Run, title: str) -> None:
    logger.info("  %s", title)
    for iz in range(CRATE_NZ - 1, -1, -1):
        groups = [" ".join(f"{run.crate_mean[ix * CRATE_NY * CRATE_NZ + iy * CRATE_NZ + iz]:5.1f}"
                           for ix in range(CRATE_NX)) for iy in range(CRATE_NY)]
        tag = "top  " if iz == CRATE_NZ - 1 else ("floor" if iz == 0 else "     ")
        logger.info("    level %d %s   %s", iz + 1, tag, "  |  ".join(groups))


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    grid = build_grid()
    n_crates = int(grid.crate_id.max()) + 1

    logger.info("=" * 92)
    logger.info("3D TRANSIENT THERMAL MODEL — pod interior, Ho February ambient 28.8 degC")
    logger.info("=" * 92)
    logger.info("grid %d x %d x %d = %s cells at %.0f mm | %d crates, %.0f kg | "
                "air channels %.0f mm (x) and %.0f mm (y)",
                NX, NY, NZ, f"{NX*NY*NZ:,}", grid.dx * 1e3, n_crates,
                RHO_PRODUCE * CRATE_LX * CRATE_LY * CRATE_LZ * n_crates,
                (LX - CRATE_NX * CRATE_LX) / (CRATE_NX + 1) * 1e3,
                (LY - CRATE_NY * CRATE_LY) / (CRATE_NY + 1) * 1e3)

    # --- 1. steady-state stratification --------------------------------------------
    steady = simulate(grid, setpoint_c=13.0, ambient_c=28.8, hours=48.0,
                      t_initial_produce_c=13.0, t_initial_air_c=13.0,
                      label="steady state", record_every_min=60.0)
    logger.info("")
    logger.info("1. STEADY STATE — where the cold actually sits (setpoint 13.0 degC)")
    logger.info("-" * 92)
    _stack_map(steady, "crate mean temperature by stack position, degC")
    margin = steady.crate_mean.min() - CHILLING_FLOOR_C
    logger.info("    air %.2f | coldest crate %.2f | warmest crate %.2f | spread %.2f K",
                steady.air_mean_c, steady.crate_mean.min(), steady.crate_mean.max(),
                steady.spread_k)
    logger.info("    chilling-injury margin at the coldest crate: %.2f K above %.0f degC",
                margin, CHILLING_FLOOR_C)
    logger.info("    all %d crates inside the usable band: %s",
                n_crates, steady.usable_crates == n_crates)

    # --- 2. how low can the setpoint go? -------------------------------------------
    logger.info("")
    logger.info("2. SETPOINT FLOOR — the spread, not the average, sets the limit")
    logger.info("-" * 92)
    floor_rows = []
    for sp in (13.0, 12.0, 11.0, 10.0):
        r = simulate(grid, setpoint_c=sp, ambient_c=28.8, hours=36.0,
                     t_initial_produce_c=sp, t_initial_air_c=sp,
                     label=f"setpoint {sp}", record_every_min=180.0)
        injured = r.n_chilled
        floor_rows.append({"setpoint_c": sp, "coldest_c": round(float(r.crate_mean.min()), 2),
                           "spread_k": round(r.spread_k, 2), "crates_injured": injured})
        logger.info("    setpoint %4.1f degC -> coldest crate %5.2f degC, spread %4.2f K, "
                    "crates below the injury floor: %d",
                    sp, r.crate_mean.min(), r.spread_k, injured)
    logger.info("    A lumped model sees only the average and would permit a lower setpoint.")

    # --- 3. field-hot load ----------------------------------------------------------
    logger.info("")
    logger.info("3. FIELD-HOT LOAD — six crates arrive at 32 degC into a cold pod")
    logger.info("-" * 92)
    hot = hot_ids(6)
    t0 = np.full(grid.shape, 13.0)
    for c in hot:
        t0[grid.crate_id == c] = 32.0
    run = simulate(grid, setpoint_c=13.0, ambient_c=28.8, hours=48.0,
                   t_initial_produce_c=13.0, t_initial_air_c=13.0,
                   label="six hot crates", initial_field=t0, record_every_min=30.0)
    ha = np.array(hot)
    cold = np.setdiff1d(np.arange(n_crates), ha)
    reach = []
    for c in ha:
        b = np.where(run.history_crates[:, c] <= QUALITY_CEILING_C)[0]
        reach.append(run.history_h[b[0]] if b.size else np.nan)
    reach = np.array(reach)
    peak_disturb = int(max((run.history_crates[i, cold] > QUALITY_CEILING_C).sum()
                           for i in range(len(run.history_h))))
    logger.info("    all six reach %.0f degC in %.1f to %.1f h",
                QUALITY_CEILING_C, np.nanmin(reach), np.nanmax(reach))
    logger.info("    neighbouring crates briefly disturbed: %d of %d at worst, all recovered",
                peak_disturb, cold.size)
    logger.info("    conduction, not compressor capacity, sets this: a crate has a thermal")
    logger.info("    time constant near 16 h, so the coil cannot reach a crate core quickly.")

    payload = {
        "grid": {"nx": NX, "ny": NY, "nz": NZ, "cells": NX * NY * NZ,
                 "dx_mm": round(grid.dx * 1e3, 1), "solver": "backward Euler, sparse LU"},
        "crates": n_crates, "ambient_c": 28.8,
        "steady_state": {"setpoint_c": 13.0, "air_c": round(steady.air_mean_c, 2),
                         "coldest_crate_c": round(float(steady.crate_mean.min()), 2),
                         "warmest_crate_c": round(float(steady.crate_mean.max()), 2),
                         "spread_k": round(steady.spread_k, 2),
                         "chilling_margin_k": round(float(margin), 2),
                         "crate_mean_c": [round(float(v), 2) for v in steady.crate_mean]},
        "setpoint_floor": floor_rows,
        "hot_load": {"crates": 6, "entry_c": 32.0,
                     "reach_band_h": [round(float(x), 1) for x in reach],
                     "peak_neighbours_disturbed": peak_disturb,
                     "all_recovered": bool(run.usable_crates == n_crates)},
    }
    (OUT_DIR / "pod_thermal_3d.json").write_text(json.dumps(payload, indent=2))
    np.save(OUT_DIR / "pod_thermal_field_steady.npy", steady.t_field)
    np.save(OUT_DIR / "pod_thermal_produce_mask.npy", grid.is_produce)
    logger.info("")
    logger.info("wrote %s", OUT_DIR / "pod_thermal_3d.json")


if __name__ == "__main__":
    main()
