"""Continental sizing engine: any African site -> a pod configuration.

The Ghana site transfer (`GHANA_SITE_TRANSFER.md`) established that a pod sized for one
site fails at another. Generalising that finding is the actual product: a deployment in
Kano, Nakuru or Nampula each needs its own array and battery, and shipping 33 bespoke
designs is not a business.

This module does three things.

1. **Sizing.** For each site it solves the two-variable problem -- array watts and battery
   kWh -- against two hard constraints: the energy balance must close in every month of
   the year, and no-sun autonomy must meet a minimum. Sizing is done at the setpoint the
   target crop requires, not at a convenient one.

2. **Robustness.** Each candidate is re-checked under a stacked pessimistic case (degraded
   envelope, heavy door traffic, soiling, high condenser approach) so the recommendation
   is not a knife-edge result.

3. **Product-line synthesis.** The per-site answers are clustered into the smallest set of
   SKUs that covers the continent within a stated tolerance. That is what converts a
   parametric model into something manufacturable.

Physics is imported from `ghana_energy_model` so there is exactly one implementation of
the energy balance in the repository.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Final

import ghana_energy_model as m
from fetch_africa_sites import CACHE_DIR, SITES, SiteSpec

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUT_DIR: Final[Path] = Path(__file__).parent / "out"

# Crop programme the pod is sized for. Solanaceous horticulture (tomato, pepper, garden
# egg) dominates smallholder perishable loss across the continent and sets a chilling
# injury floor near 13 degC; a cool programme covers brassicas, carrots and temperate
# highland vegetables.
SETPOINT_WARM_C: Final[float] = 13.0
SETPOINT_COOL_C: Final[float] = 6.0

MIN_AUTONOMY_H: Final[float] = 48.0
ARRAY_STEP_W: Final[float] = 10.0
ARRAY_MAX_W: Final[float] = 1600.0
BATTERY_STEPS_KWH: Final[tuple[float, ...]] = (2.4, 3.6, 4.8, 7.2, 9.6, 12.0)

# Stacked pessimistic case, applied simultaneously.
STRESS: Final[tuple[tuple[str, float], ...]] = (
    ("UA_W_PER_K", 2.5),
    ("DOOR_OPENINGS_PER_DAY", 32),
    ("SOILING_EFF_HARMATTAN", 0.85),
    ("SOILING_EFF_CLEAN", 0.90),
    ("COND_APPROACH_K", 15.0),
)

# Cost model, USD at 10k-unit volume. The three constants below are calibrated against a
# costed v1.2 hardware specification rather than assumed: at volume a 330 W array comes to
# $52 ($0.158/W) and a 4.8 kWh LiFePO4 pack to $230 ($47.9/kWh), against a $586 pod total,
# which leaves $304 for structure, insulation, refrigeration, controls and assembly. That
# calibration matters: an assumed cost model put HG-C at $839 against a costed $611, a 37%
# spread, and since the SKU partition below minimises cost the partition inherits any error
# here. The underlying component costing is not part of this repository.
COST_BASE_USD: Final[float] = 304.0        # pod less array and battery
COST_PV_USD_PER_W: Final[float] = 0.158
COST_BATTERY_USD_PER_KWH: Final[float] = 47.9


def tilt_for(lat: float) -> float:
    """Fixed tilt: latitude, floored so panels still shed dust and rain."""
    return max(abs(lat), 8.0)


def load_site(spec: SiteSpec) -> m.Site:
    """Build a model Site from the cached NASA POWER response."""
    return m.Site.from_power_json(
        CACHE_DIR / f"{spec.key}.json", spec.name, tilt_for(spec.lat), harmattan=spec.harmattan
    )


def _stressed(fn: Callable[[], float]) -> float:
    original = {a: getattr(m, a) for a, _ in STRESS}
    for attr, val in STRESS:
        setattr(m, attr, val)
    try:
        return fn()
    finally:
        for attr, val in original.items():
            setattr(m, attr, val)


def worst_margin(site: m.Site, sp: float, array_w: float, battery_kwh: float) -> float:
    return min(r.margin_wh for r in m.run_site(site, sp, array_w, battery_kwh))


def worst_autonomy(site: m.Site, sp: float, array_w: float, battery_kwh: float) -> float:
    return min(r.autonomy_h for r in m.run_site(site, sp, array_w, battery_kwh))


def peak_demand_wh(site: m.Site, sp: float) -> float:
    return max(m.electrical_demand_wh_day(sm, sp)[0] for sm in site.months)


@dataclass(frozen=True, slots=True)
class Sizing:
    """The configuration a site requires, and what it costs."""

    key: str
    name: str
    country: str
    iso: str
    zone: str
    lat: float
    lon: float
    setpoint_c: float
    annual_ghi: float
    mean_temp_c: float
    worst_month: str
    peak_demand_wh: float
    min_array_w: float
    array_w: float
    battery_kwh: float
    autonomy_h: float
    margin_wh: float
    stressed_margin_wh: float
    cost_usd: float

    @property
    def closes_stressed(self) -> bool:
        return self.stressed_margin_wh >= 0.0


def size_site(spec: SiteSpec, setpoint_c: float) -> Sizing:
    """Solve array and battery for one site under both constraints."""
    site = load_site(spec)

    # Battery is set first: it must carry the worst month's demand for MIN_AUTONOMY_H.
    battery = BATTERY_STEPS_KWH[-1]
    for cand in BATTERY_STEPS_KWH:
        if worst_autonomy(site, setpoint_c, ARRAY_MAX_W, cand) >= MIN_AUTONOMY_H:
            battery = cand
            break

    # Minimum array that closes every month nominally.
    min_array = float("nan")
    array = ARRAY_STEP_W
    while array <= ARRAY_MAX_W:
        if worst_margin(site, setpoint_c, array, battery) >= 0:
            min_array = array
            break
        array += ARRAY_STEP_W

    # Recommended array: smallest that also survives the stacked pessimistic case.
    rec = min_array if min_array == min_array else ARRAY_MAX_W
    while rec <= ARRAY_MAX_W:
        if _stressed(lambda: worst_margin(site, setpoint_c, rec, battery)) >= 0:
            break
        rec += ARRAY_STEP_W

    rows = m.run_site(site, setpoint_c, rec, battery)
    worst = min(rows, key=lambda r: r.margin_wh)
    cost = COST_BASE_USD + rec * COST_PV_USD_PER_W + battery * COST_BATTERY_USD_PER_KWH

    return Sizing(
        key=spec.key, name=spec.name, country=spec.country, iso=spec.iso, zone=spec.zone,
        lat=spec.lat, lon=spec.lon, setpoint_c=setpoint_c,
        annual_ghi=round(sum(s.ghi_kwh_m2_day for s in site.months) / 12, 2),
        mean_temp_c=round(sum(s.t_mean_c for s in site.months) / 12, 1),
        worst_month=worst.month,
        peak_demand_wh=round(peak_demand_wh(site, setpoint_c)),
        min_array_w=min_array, array_w=rec, battery_kwh=battery,
        autonomy_h=round(worst.autonomy_h), margin_wh=round(worst.margin_wh),
        stressed_margin_wh=round(_stressed(lambda: worst_margin(site, setpoint_c, rec, battery))),
        cost_usd=round(cost),
    )


# ---------------------------------------------------------------------------------
# Product line synthesis
# ---------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Sku:
    """One manufacturable configuration covering a band of sites."""

    name: str
    array_w: float
    battery_kwh: float
    cost_usd: float
    sites: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.sites)


def optimal_bands(values: list[float], k: int, cost_per_w: float) -> tuple[list[float], float]:
    """Smallest total oversizing cost when covering `values` with `k` SKU tiers.

    Exact dynamic program over the sorted distinct requirements. Every site must be served
    by a tier at or above its requirement, so a tier's ceiling is the largest requirement
    it covers; the waste for a member is (tier - requirement) * cost per watt.
    """
    v = sorted(values)
    n = len(v)
    # waste[i][j] = cost of one tier covering v[i..j] inclusive, capped at v[j]
    waste = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            waste[i][j] = sum(v[j] - v[t] for t in range(i, j + 1)) * cost_per_w
    INF = float("inf")
    best = [[INF] * (k + 1) for _ in range(n + 1)]
    back = [[-1] * (k + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for j in range(1, n + 1):
        for tiers in range(1, k + 1):
            for i in range(j):
                if best[i][tiers - 1] == INF:
                    continue
                cand = best[i][tiers - 1] + waste[i][j - 1]
                if cand < best[j][tiers]:
                    best[j][tiers] = cand
                    back[j][tiers] = i
    cuts: list[float] = []
    j, tiers = n, k
    while tiers > 0:
        i = back[j][tiers]
        cuts.append(v[j - 1])
        j, tiers = i, tiers - 1
    return sorted(cuts), best[n][k]


def synthesise_skus(sizings: list[Sizing], bands: tuple[float, ...]) -> list[Sku]:
    """Assign every site to the smallest tier that satisfies it.

    Rounding up is the only safe direction: an undersized pod browns out, an oversized one
    only costs more. Battery per SKU is the maximum any member site requires.
    """
    skus: list[Sku] = []
    for i, top in enumerate(bands):
        low = bands[i - 1] if i else 0.0
        members = [s for s in sizings if low < s.array_w <= top]
        if not members:
            continue
        batt = max(s.battery_kwh for s in members)
        cost = COST_BASE_USD + top * COST_PV_USD_PER_W + batt * COST_BATTERY_USD_PER_KWH
        skus.append(Sku(name=f"HG-{chr(65 + len(skus))}", array_w=top, battery_kwh=batt,
                        cost_usd=round(cost), sites=tuple(s.key for s in members)))
    return skus


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    logger.info("=" * 104)
    logger.info("CONTINENTAL SIZING — %d sites, solanaceous programme (%.0f degC), "
                "min autonomy %.0f h", len(SITES), SETPOINT_WARM_C, MIN_AUTONOMY_H)
    logger.info("=" * 104)
    logger.info("%-11s %-20s %-19s %5s %6s %7s %6s %6s %6s %5s %7s",
                "site", "region", "zone", "GHI", "T", "demand", "min W", "spec W",
                "batt", "aut", "cost $")

    warm = [size_site(s, SETPOINT_WARM_C) for s in SITES]
    for z in sorted({s.zone for s in warm}):
        for s in [x for x in warm if x.zone == z]:
            flag = "" if s.closes_stressed else "  <-- stressed deficit"
            logger.info("%-11s %-20s %-19s %5.2f %5.1fC %7.0f %6.0f %6.0f %5.1f %5.0f %7.0f%s",
                        s.key, f"{s.name}, {s.iso}", s.zone, s.annual_ghi, s.mean_temp_c,
                        s.peak_demand_wh, s.min_array_w, s.array_w, s.battery_kwh,
                        s.autonomy_h, s.cost_usd, flag)

    lo = min(warm, key=lambda s: s.array_w)
    hi = max(warm, key=lambda s: s.array_w)
    logger.info("-" * 104)
    logger.info("Array requirement spans %.0f W (%s) to %.0f W (%s) — a %.1fx range.",
                lo.array_w, lo.name, hi.array_w, hi.name, hi.array_w / lo.array_w)
    logger.info("Cost spans $%.0f to $%.0f. Sizing every site at the continental maximum "
                "would waste $%.0f per pod at the easy end.",
                lo.cost_usd, hi.cost_usd, hi.cost_usd - lo.cost_usd)

    logger.info("")
    logger.info("=" * 104)
    logger.info("PRODUCT LINE — smallest SKU set covering all %d sites", len(warm))
    logger.info("=" * 104)
    reqs = [s.array_w for s in warm]
    logger.info("%-6s %-46s %12s %10s", "SKUs", "array tiers (W)", "waste $/pod", "vs bespoke")
    curve = []
    for k in range(1, 6):
        cuts, total = optimal_bands(reqs, k, COST_PV_USD_PER_W)
        per_pod = total / len(reqs)
        curve.append((k, cuts, per_pod))
        logger.info("%-6d %-46s %11.2f %9.1f%%",
                    k, ", ".join(f"{c:.0f}" for c in cuts), per_pod,
                    100 * per_pod / (sum(s.cost_usd for s in warm) / len(warm)))
    logger.info("")
    chosen_k = 3
    bands = tuple(curve[chosen_k - 1][1])
    skus = synthesise_skus(warm, bands)
    logger.info("Selected %d SKUs (the knee of the curve above):", chosen_k)
    for sku in skus:
        logger.info("  %-6s %5.0f W  %4.1f kWh  $%4.0f  covers %2d sites: %s",
                    sku.name, sku.array_w, sku.battery_kwh, sku.cost_usd, sku.count,
                    ", ".join(sku.sites[:10]) + (" ..." if sku.count > 10 else ""))
    ideal = sum(s.cost_usd for s in warm) / len(warm)
    actual = sum(next(k.cost_usd for k in skus if s.key in k.sites) for s in warm) / len(warm)
    logger.info("-" * 104)
    logger.info("%d SKUs cover all %d sites. Mean pod cost $%.0f vs $%.0f bespoke: "
                "a %.1f%% premium for manufacturability.",
                len(skus), len(warm), actual, ideal, 100 * (actual - ideal) / ideal)

    logger.info("")
    logger.info("=" * 104)
    logger.info("COOL PROGRAMME (%.0f degC) — brassicas, carrots, temperate highland veg",
                SETPOINT_COOL_C)
    logger.info("=" * 104)
    cool = [size_site(s, SETPOINT_COOL_C) for s in SITES]
    infeasible = [s for s in cool if s.array_w >= ARRAY_MAX_W]
    logger.info("Median array %.0f W warm vs %.0f W cool. %d of %d sites cannot hold "
                "%.0f degC within the 1.6 kW array ceiling.",
                sorted(s.array_w for s in warm)[len(warm) // 2],
                sorted(s.array_w for s in cool)[len(cool) // 2],
                len(infeasible), len(cool), SETPOINT_COOL_C)
    if infeasible:
        logger.info("  infeasible: %s", ", ".join(s.key for s in infeasible))

    payload = {
        "generated": "2026-08-18",
        "constraints": {"min_autonomy_h": MIN_AUTONOMY_H, "closes_every_month": True,
                        "stress_case": dict(STRESS)},
        "setpoint_warm_c": SETPOINT_WARM_C, "setpoint_cool_c": SETPOINT_COOL_C,
        "sites_warm": [asdict(s) for s in warm],
        "sites_cool": [asdict(s) for s in cool],
        "skus": [asdict(k) for k in skus],
        "sku_curve": [{"k": k, "tiers": c, "waste_usd_per_pod": round(w, 2)} for k, c, w in curve],
    }
    (OUT_DIR / "africa_sizing.json").write_text(json.dumps(payload, indent=2))
    logger.info("")
    logger.info("wrote %s", OUT_DIR / "africa_sizing.json")


if __name__ == "__main__":
    main()
