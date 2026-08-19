"""Site-transfer energy model for the HarvestGuard pod: Meru (Kenya) -> Ho (Volta, Ghana).

The v1.2 hardware spec (`hardware_spec.md`) sizes the PV array and battery against Meru,
Kenya: a high-altitude equatorial site with ~6.19 kWh/m2/day annual GHI and a 17.6 degC
annual mean air temperature. The CircularEconomy4Ghana pilot site is Ho, Volta Region,
Ghana: a humid lowland tropical site with ~4.91 kWh/m2/day and a 26.7 degC annual mean.

That is 21% less solar resource against a ~9 K hotter ambient, which moves the energy
balance in both directions at once. This module re-derives the balance month by month so
the Ghana configuration is sized from first principles rather than inherited.

Model chain
-----------
supply  : GHI -> plane-of-array -> cell-temperature derate -> soiling -> BOS -> Wh/day
demand  : conduction + infiltration (sensible + latent) + product pull-down + respiration
          -> thermal Wh/day -> divided by a condensing-temperature-dependent COP
verdict : per-month surplus/deficit, and the smallest array that closes the worst month

Assumptions are declared as module constants so every number in the output is traceable.
Climatology is NASA POWER monthly climatology (see `data/*.json`), not modelled weather.

Limitations of the claim
------------------------
This is a steady-state monthly-mean energy balance, not an hourly dynamic simulation. It
sizes equipment and identifies whether a configuration closes; it does not predict the
temperature trace of a specific day. Latent load is estimated from psychrometrics at
monthly-mean RH, which understates the load during a wet-season rain event.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATA_DIR: Final[Path] = Path(__file__).parent / "data"
MONTHS: Final[tuple[str, ...]] = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)

# --- PV sub-model -----------------------------------------------------------------
NOCT_C: Final[float] = 45.0                 # nominal operating cell temperature, degC
NOCT_IRRADIANCE: Final[float] = 800.0       # W/m2, NOCT reference
TEMP_COEFF_PMAX: Final[float] = -0.0038     # /K, mono-Si power temperature coefficient
STC_CELL_TEMP_C: Final[float] = 25.0
WIRING_MISMATCH_EFF: Final[float] = 0.95
MPPT_EFF: Final[float] = 0.97
BATTERY_ROUNDTRIP_EFF: Final[float] = 0.95  # most load is served overnight from storage
SOILING_EFF_CLEAN: Final[float] = 0.96
SOILING_EFF_HARMATTAN: Final[float] = 0.90  # Dec-Feb dust haze over West Africa
HARMATTAN_MONTHS: Final[frozenset[str]] = frozenset({"DEC", "JAN", "FEB"})
DAYTIME_AMBIENT_OFFSET_K: Final[float] = 4.0  # daytime mean above 24 h mean

# --- Envelope / load sub-model ----------------------------------------------------
UA_W_PER_K: Final[float] = 2.0              # 80 mm PU on 6 sides, per the digital twin
USABLE_VOLUME_M3: Final[float] = 1.0
DOOR_OPENINGS_PER_DAY: Final[int] = 24      # village pod at an aggregation point
AIR_EXCHANGE_FRACTION: Final[float] = 0.35  # of box volume displaced per opening
AIR_DENSITY_KG_PER_M3: Final[float] = 1.16
AIR_CP_J_PER_KG_K: Final[float] = 1006.0
WATER_LATENT_HEAT_J_PER_KG: Final[float] = 2.45e6
INTERNAL_RH: Final[float] = 0.90            # target for horticultural produce
CRATE_MASS_KG: Final[float] = 10.0
CRATES_LOADED_PER_DAY: Final[int] = 6       # turnover on a 40-crate pod
PRODUCE_CP_J_PER_KG_K: Final[float] = 3900.0
FIELD_HEAT_ENTRY_TEMP_OFFSET_K: Final[float] = 6.0  # produce arrives above ambient mean
RESPIRATION_W_PER_TONNE_AT_10C: Final[float] = 55.0  # mixed horticulture, ASHRAE order
RESPIRATION_Q10: Final[float] = 2.4
FAN_CONTROLS_W: Final[float] = 4.0          # evaporator fan duty + MCU + modem, average

# --- Compressor sub-model ---------------------------------------------------------
EVAP_APPROACH_K: Final[float] = 8.0         # evaporator below box setpoint
COND_APPROACH_K: Final[float] = 12.0        # condensing above daytime ambient
COP_REF: Final[float] = 1.8                 # datasheet point
COP_REF_SETPOINT_C: Final[float] = 4.0
COP_REF_AMBIENT_C: Final[float] = 35.0

# --- Installed hardware (v1.2, Meru-sized) ----------------------------------------
PV_STC_W_V12: Final[float] = 330.0
BATTERY_KWH_V12: Final[float] = 4.8
BATTERY_USABLE_DOD: Final[float] = 0.85


def _carnot_fraction() -> float:
    """Back out the compressor's Carnot efficiency from the datasheet operating point."""
    t_evap = COP_REF_SETPOINT_C - EVAP_APPROACH_K + 273.15
    t_cond = COP_REF_AMBIENT_C + COND_APPROACH_K + 273.15
    return COP_REF / (t_evap / (t_cond - t_evap))


CARNOT_FRACTION: Final[float] = _carnot_fraction()


def cop(setpoint_c: float, ambient_daytime_c: float) -> float:
    """Coefficient of performance at a given box setpoint and ambient temperature."""
    t_evap = setpoint_c - EVAP_APPROACH_K + 273.15
    t_cond = ambient_daytime_c + COND_APPROACH_K + 273.15
    if t_cond <= t_evap:
        raise ValueError("condensing temperature must exceed evaporating temperature")
    return CARNOT_FRACTION * (t_evap / (t_cond - t_evap))


def saturation_pressure_pa(temp_c: float) -> float:
    """Saturation vapour pressure of water (Tetens equation), Pa."""
    return 610.78 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def humidity_ratio(temp_c: float, rh: float, pressure_pa: float = 101_325.0) -> float:
    """Absolute humidity ratio, kg water per kg dry air."""
    p_v = rh * saturation_pressure_pa(temp_c)
    return 0.622 * p_v / (pressure_pa - p_v)


@dataclass(frozen=True, slots=True)
class SiteMonth:
    """One month of NASA POWER climatology at a site."""

    month: str
    ghi_kwh_m2_day: float
    t_mean_c: float
    t_max_c: float
    rh_pct: float

    @property
    def t_daytime_c(self) -> float:
        return self.t_mean_c + DAYTIME_AMBIENT_OFFSET_K


@dataclass(frozen=True, slots=True)
class Site:
    """A candidate deployment site."""

    name: str
    latitude: float
    tilt_deg: float
    months: tuple[SiteMonth, ...]
    harmattan: bool

    @classmethod
    def from_power_json(cls, path: Path, name: str, tilt_deg: float, harmattan: bool) -> "Site":
        raw = json.loads(path.read_text())
        params = raw["properties"]["parameter"]
        lat = raw["geometry"]["coordinates"][1]
        months = tuple(
            SiteMonth(
                month=m,
                ghi_kwh_m2_day=params["ALLSKY_SFC_SW_DWN"][m],
                t_mean_c=params["T2M"][m],
                t_max_c=params["T2M_MAX"][m],
                rh_pct=params["RH2M"][m],
            )
            for m in MONTHS
        )
        return cls(name=name, latitude=lat, tilt_deg=tilt_deg, months=months, harmattan=harmattan)


def poa_gain(latitude: float, tilt_deg: float) -> float:
    """Plane-of-array gain over GHI for a low-latitude fixed tilt.

    At these latitudes the optimal tilt is shallow and the annual gain is small; a
    first-order cosine approximation is adequate and deliberately conservative.
    """
    return 1.0 + 0.015 * math.cos(math.radians(abs(latitude) - tilt_deg))


def pv_yield_wh_day(site: Site, sm: SiteMonth, array_w: float) -> float:
    """Daily usable DC energy delivered to the load from a fixed array."""
    poa = sm.ghi_kwh_m2_day * poa_gain(site.latitude, site.tilt_deg)
    irradiance_mean = poa * 1000.0 / 8.0  # spread over an 8 h effective window, W/m2
    cell_temp = sm.t_daytime_c + (NOCT_C - 20.0) / NOCT_IRRADIANCE * irradiance_mean
    temp_derate = 1.0 + TEMP_COEFF_PMAX * (cell_temp - STC_CELL_TEMP_C)
    soiling = (
        SOILING_EFF_HARMATTAN
        if (site.harmattan and sm.month in HARMATTAN_MONTHS)
        else SOILING_EFF_CLEAN
    )
    bos = WIRING_MISMATCH_EFF * MPPT_EFF * BATTERY_ROUNDTRIP_EFF
    return array_w * poa * temp_derate * soiling * bos


def thermal_load_wh_day(sm: SiteMonth, setpoint_c: float) -> dict[str, float]:
    """Break the daily thermal load into its physical components, Wh/day."""
    conduction = UA_W_PER_K * (sm.t_mean_c - setpoint_c) * 24.0

    exchange_m3 = USABLE_VOLUME_M3 * AIR_EXCHANGE_FRACTION * DOOR_OPENINGS_PER_DAY
    air_kg = exchange_m3 * AIR_DENSITY_KG_PER_M3
    sensible_j = air_kg * AIR_CP_J_PER_KG_K * (sm.t_daytime_c - setpoint_c)
    w_out = humidity_ratio(sm.t_daytime_c, sm.rh_pct / 100.0)
    w_in = humidity_ratio(setpoint_c, INTERNAL_RH)
    latent_j = air_kg * max(w_out - w_in, 0.0) * WATER_LATENT_HEAT_J_PER_KG
    infiltration_sensible = sensible_j / 3600.0
    infiltration_latent = latent_j / 3600.0

    pulldown_j = (
        CRATES_LOADED_PER_DAY
        * CRATE_MASS_KG
        * PRODUCE_CP_J_PER_KG_K
        * (sm.t_mean_c + FIELD_HEAT_ENTRY_TEMP_OFFSET_K - setpoint_c)
    )
    pulldown = max(pulldown_j / 3600.0, 0.0)

    stored_tonnes = CRATE_MASS_KG * 40 * 0.55 / 1000.0  # 55% utilisation
    resp_w = (
        stored_tonnes
        * RESPIRATION_W_PER_TONNE_AT_10C
        * RESPIRATION_Q10 ** ((setpoint_c - 10.0) / 10.0)
    )
    respiration = resp_w * 24.0

    total = conduction + infiltration_sensible + infiltration_latent + pulldown + respiration
    return {
        "conduction": conduction,
        "infiltration_sensible": infiltration_sensible,
        "infiltration_latent": infiltration_latent,
        "pulldown": pulldown,
        "respiration": respiration,
        "total_thermal": total,
    }


def electrical_demand_wh_day(sm: SiteMonth, setpoint_c: float) -> tuple[float, dict[str, float]]:
    """Convert the thermal load to electrical draw at the month's operating COP."""
    parts = thermal_load_wh_day(sm, setpoint_c)
    month_cop = cop(setpoint_c, sm.t_daytime_c)
    compressor = parts["total_thermal"] / month_cop
    parasitics = FAN_CONTROLS_W * 24.0
    parts["cop"] = month_cop
    parts["compressor_electrical"] = compressor
    parts["parasitics"] = parasitics
    return compressor + parasitics, parts


@dataclass(slots=True)
class MonthResult:
    month: str
    supply_wh: float
    demand_wh: float
    cop: float
    autonomy_h: float
    parts: dict[str, float] = field(repr=False, default_factory=dict)

    @property
    def margin_wh(self) -> float:
        return self.supply_wh - self.demand_wh

    @property
    def closes(self) -> bool:
        return self.margin_wh >= 0.0


def run_site(site: Site, setpoint_c: float, array_w: float, battery_kwh: float) -> list[MonthResult]:
    """Month-by-month energy balance for one configuration at one site."""
    results: list[MonthResult] = []
    for sm in site.months:
        supply = pv_yield_wh_day(site, sm, array_w)
        demand, parts = electrical_demand_wh_day(sm, setpoint_c)
        autonomy = battery_kwh * 1000.0 * BATTERY_USABLE_DOD / demand * 24.0
        results.append(
            MonthResult(
                month=sm.month,
                supply_wh=supply,
                demand_wh=demand,
                cop=parts["cop"],
                autonomy_h=autonomy,
                parts=parts,
            )
        )
    return results


def min_array_to_close(site: Site, setpoint_c: float, step_w: float = 5.0, cap_w: float = 1200.0) -> float:
    """Smallest STC array rating that closes every month at this site and setpoint."""
    array = step_w
    while array <= cap_w:
        if all(r.closes for r in run_site(site, setpoint_c, array, BATTERY_KWH_V12)):
            return array
        array += step_w
    return float("nan")


def _report(site: Site, setpoint_c: float, array_w: float, label: str) -> list[MonthResult]:
    rows = run_site(site, setpoint_c, array_w, BATTERY_KWH_V12)
    worst = min(rows, key=lambda r: r.margin_wh)
    logger.info("")
    logger.info("=" * 78)
    logger.info("%s | %s | setpoint %.0f degC | array %.0f W | battery %.1f kWh",
                label, site.name, setpoint_c, array_w, BATTERY_KWH_V12)
    logger.info("=" * 78)
    logger.info("%-5s %9s %9s %9s %7s %9s", "mon", "supply", "demand", "margin", "COP", "autonomy")
    for r in rows:
        logger.info("%-5s %9.0f %9.0f %9.0f %7.2f %8.0f h%s",
                    r.month, r.supply_wh, r.demand_wh, r.margin_wh, r.cop, r.autonomy_h,
                    "" if r.closes else "   <-- DEFICIT")
    logger.info("-" * 78)
    logger.info("worst month %s: margin %+.0f Wh/day (%.0f%% of demand), autonomy %.0f h",
                worst.month, worst.margin_wh, 100.0 * worst.margin_wh / worst.demand_wh,
                worst.autonomy_h)
    p = worst.parts
    logger.info("worst-month thermal split: conduction %.0f | infil-sens %.0f | infil-LATENT %.0f "
                "| pulldown %.0f | respiration %.0f Wh_th/day",
                p["conduction"], p["infiltration_sensible"], p["infiltration_latent"],
                p["pulldown"], p["respiration"])
    return rows


def main() -> None:
    meru = Site.from_power_json(DATA_DIR / "meru_kenya_climatology.json", "Meru, Kenya", 5.0, harmattan=False)
    ho = Site.from_power_json(DATA_DIR / "ho_ghana_climatology.json", "Ho, Volta, Ghana", 8.0, harmattan=True)

    logger.info("Carnot fraction back-calculated from datasheet point: %.3f", CARNOT_FRACTION)

    # 1. The design as specified, at its design site.
    _report(meru, 8.0, PV_STC_W_V12, "BASELINE (v1.2 as specified)")

    # 2. The same hardware moved to Ghana, unchanged, at the same setpoint.
    _report(ho, 8.0, PV_STC_W_V12, "SITE TRANSFER (no change)")

    # 3. Ghana at the setpoint the Ghanaian crop actually requires.
    #    Tomato/pepper/eggplant suffer chilling injury below ~10 degC (USDA), so the
    #    Ghana pilot runs the warm end of the 4-13 degC band.
    _report(ho, 13.0, PV_STC_W_V12, "GHANA CONFIG (13 degC solanaceous setpoint)")

    logger.info("")
    logger.info("=" * 78)
    logger.info("MINIMUM ARRAY TO CLOSE EVERY MONTH")
    logger.info("=" * 78)
    for site, sp in ((meru, 8.0), (ho, 8.0), (ho, 13.0), (ho, 10.0)):
        need = min_array_to_close(site, sp)
        logger.info("%-22s setpoint %4.0f degC -> %6.0f W STC required (installed: %.0f W)",
                    site.name, sp, need, PV_STC_W_V12)


if __name__ == "__main__":
    main()
