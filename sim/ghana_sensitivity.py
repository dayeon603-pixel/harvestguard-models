"""Sensitivity and robustness check on the Ho (Ghana) site-transfer result.

The headline claim from `ghana_energy_model.py` is that the Meru-sized 330 W / 4.8 kWh
configuration runs an energy deficit in every month at Ho, and that the Ghana
configuration needs a 13 degC setpoint plus a larger array. A single point estimate is
not a finding. This module perturbs each assumption independently and reports whether the
conclusion flips, then writes the deck-ready figure and a results table.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import ghana_energy_model as m  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUT_DIR: Final[Path] = Path(__file__).parent / "out"
GHANA_SETPOINT_C: Final[float] = 13.0
CANDIDATE_ARRAY_W: Final[float] = 450.0


@dataclass(frozen=True, slots=True)
class Perturbation:
    """One independent assumption swing."""

    name: str
    attr: str
    low: float
    high: float


PERTURBATIONS: Final[tuple[Perturbation, ...]] = (
    Perturbation("envelope UA (W/K)", "UA_W_PER_K", 1.5, 3.0),
    Perturbation("door openings / day", "DOOR_OPENINGS_PER_DAY", 12, 40),
    Perturbation("crates loaded / day", "CRATES_LOADED_PER_DAY", 3, 10),
    Perturbation("soiling eff (Harmattan)", "SOILING_EFF_HARMATTAN", 0.82, 0.95),
    Perturbation("PV temp coeff (/K)", "TEMP_COEFF_PMAX", -0.0045, -0.0030),
    Perturbation("condenser approach (K)", "COND_APPROACH_K", 8.0, 16.0),
    Perturbation("battery round-trip eff", "BATTERY_ROUNDTRIP_EFF", 0.90, 0.98),
    Perturbation("field-heat entry offset (K)", "FIELD_HEAT_ENTRY_TEMP_OFFSET_K", 3.0, 9.0),
)


def _with_override(attr: str, value: float, fn: Callable[[], float]) -> float:
    original = getattr(m, attr)
    setattr(m, attr, value)
    try:
        return fn()
    finally:
        setattr(m, attr, original)


def worst_margin(site: m.Site, setpoint_c: float, array_w: float) -> float:
    """Worst-month energy margin in Wh/day for a configuration."""
    return min(r.margin_wh for r in m.run_site(site, setpoint_c, array_w, m.BATTERY_KWH_V12))


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    ho = m.Site.from_power_json(
        m.DATA_DIR / "ho_ghana_climatology.json", "Ho, Volta, Ghana", 8.0, harmattan=True
    )
    meru = m.Site.from_power_json(
        m.DATA_DIR / "meru_kenya_climatology.json", "Meru, Kenya", 5.0, harmattan=False
    )

    logger.info("=" * 84)
    logger.info("SENSITIVITY: does the 'Kenya design fails at Ho' conclusion survive?")
    logger.info("=" * 84)
    logger.info("%-30s %14s %14s %10s", "assumption swung", "low -> margin", "high -> margin", "flips?")

    flips = 0
    rows: list[dict[str, object]] = []
    for p in PERTURBATIONS:
        lo = _with_override(p.attr, p.low, lambda: worst_margin(ho, 8.0, m.PV_STC_W_V12))
        hi = _with_override(p.attr, p.high, lambda: worst_margin(ho, 8.0, m.PV_STC_W_V12))
        flipped = lo >= 0 or hi >= 0
        flips += int(flipped)
        rows.append({"assumption": p.name, "low_margin_wh": round(lo), "high_margin_wh": round(hi),
                     "flips": flipped})
        logger.info("%-30s %+13.0f %+14.0f %10s", p.name, lo, hi, "YES" if flipped else "no")

    logger.info("-" * 84)
    logger.info("%d of %d single-assumption swings flip the conclusion.", flips, len(PERTURBATIONS))

    logger.info("")
    logger.info("=" * 84)
    logger.info("CANDIDATE GHANA CONFIGURATION")
    logger.info("=" * 84)
    for label, site, sp, arr in (
        ("Meru baseline, as built", meru, 8.0, m.PV_STC_W_V12),
        ("Ho, unchanged hardware", ho, 8.0, m.PV_STC_W_V12),
        ("Ho, 13 degC setpoint only", ho, GHANA_SETPOINT_C, m.PV_STC_W_V12),
        (f"Ho, 13 degC + {CANDIDATE_ARRAY_W:.0f} W array", ho, GHANA_SETPOINT_C, CANDIDATE_ARRAY_W),
    ):
        res = m.run_site(site, sp, arr, m.BATTERY_KWH_V12)
        worst = min(res, key=lambda r: r.margin_wh)
        logger.info("%-28s worst month %s  margin %+6.0f Wh/day  autonomy %3.0f h  %s",
                    label, worst.month, worst.margin_wh, worst.autonomy_h,
                    "CLOSES" if worst.closes else "DEFICIT")

    # Robustness of the recommended configuration under a simultaneous pessimistic case.
    def pessimistic() -> float:
        return worst_margin(ho, GHANA_SETPOINT_C, CANDIDATE_ARRAY_W)

    stacked = pessimistic
    for attr, val in (("UA_W_PER_K", 2.5), ("DOOR_OPENINGS_PER_DAY", 32),
                      ("SOILING_EFF_HARMATTAN", 0.85), ("COND_APPROACH_K", 15.0)):
        prev = stacked
        stacked = (lambda a=attr, v=val, f=prev: _with_override(a, v, f))
    logger.info("")
    logger.info("Recommended config under a STACKED pessimistic case "
                "(UA 2.5, 32 openings/day, 15%% soiling, 15 K condenser approach): "
                "worst-month margin %+.0f Wh/day", stacked())

    ghana_min_array = m.min_array_to_close(ho, GHANA_SETPOINT_C)
    payload = {
        "site_ho_ghana": {"lat": ho.latitude, "annual_ghi_kwh_m2_day": round(
            sum(s.ghi_kwh_m2_day for s in ho.months) / 12, 2)},
        "site_meru_kenya": {"lat": meru.latitude, "annual_ghi_kwh_m2_day": round(
            sum(s.ghi_kwh_m2_day for s in meru.months) / 12, 2)},
        "unchanged_hardware_at_ho_worst_margin_wh": round(worst_margin(ho, 8.0, m.PV_STC_W_V12)),
        "min_array_w_ho_13c": ghana_min_array,
        "recommended_array_w": CANDIDATE_ARRAY_W,
        "recommended_setpoint_c": GHANA_SETPOINT_C,
        "sensitivity": rows,
        "flips_of_conclusion": flips,
    }
    (OUT_DIR / "ghana_energy_results.json").write_text(json.dumps(payload, indent=2))

    _plot(ho, meru)
    logger.info("")
    logger.info("wrote %s and %s", OUT_DIR / "ghana_energy_results.json",
                OUT_DIR / "ghana_energy_balance.png")


def _plot(ho: m.Site, meru: m.Site) -> None:
    """Deck-ready figure: supply vs demand at Ho, before and after the Ghana re-spec."""
    unchanged = m.run_site(ho, 8.0, m.PV_STC_W_V12, m.BATTERY_KWH_V12)
    fixed = m.run_site(ho, GHANA_SETPOINT_C, CANDIDATE_ARRAY_W, m.BATTERY_KWH_V12)

    ink, terra, green, line = "#2c2a26", "#b85f2a", "#3b7a57", "#e7dfd0"
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.1), sharey=True)
    fig.patch.set_facecolor("#faf6ee")

    for ax, rows, title in (
        (axes[0], unchanged, "Kenya-sized pod at Ho\n330 W, 8 °C setpoint"),
        (axes[1], fixed, "Ghana configuration\n450 W, 13 °C setpoint"),
    ):
        ax.set_facecolor("#faf6ee")
        x = range(12)
        ax.plot(x, [r.supply_wh for r in rows], color=green, lw=2.2, marker="o", ms=4,
                label="solar supply")
        ax.plot(x, [r.demand_wh for r in rows], color=terra, lw=2.2, marker="s", ms=4,
                label="pod demand")
        ax.fill_between(x, [r.supply_wh for r in rows], [r.demand_wh for r in rows],
                        where=[r.supply_wh < r.demand_wh for r in rows],
                        color=terra, alpha=0.18, interpolate=True)
        ax.fill_between(x, [r.supply_wh for r in rows], [r.demand_wh for r in rows],
                        where=[r.supply_wh >= r.demand_wh for r in rows],
                        color=green, alpha=0.15, interpolate=True)
        ax.set_xticks(list(x))
        ax.set_xticklabels([mm[0] for mm in m.MONTHS], fontsize=9, color=ink)
        ax.set_title(title, fontsize=11, color=ink, pad=10)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(line)
        ax.tick_params(colors=ink, labelsize=9)
        ax.grid(axis="y", color=line, lw=0.8)
        ax.set_axisbelow(True)

    axes[0].set_ylabel("Wh / day", fontsize=10, color=ink)
    axes[0].legend(frameon=False, fontsize=9, loc="lower right")
    fig.suptitle("HarvestGuard energy balance, Ho (Volta Region) — NASA POWER climatology",
                 fontsize=12.5, color=ink, y=1.02)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "ghana_energy_balance.png", dpi=190, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    main()
