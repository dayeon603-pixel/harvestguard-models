"""Hour-by-hour dynamic simulation of the pod at Ho, Volta Region, on real 2024 weather.

The monthly-mean energy balance in `ghana_energy_model.py` answers a sizing question: does
average supply cover average demand. It cannot answer the question a farmer actually cares
about, which is whether the box holds temperature on the worst week of the year.

This module integrates the pod's state through 8,784 hours of NASA POWER hourly weather for
Ho (2024), at five-minute sub-steps, tracking two state variables:

  T_box   box air/produce temperature, driven by conduction, solar gain on the envelope,
          discrete door-opening events, field-hot produce arriving, respiration, and
          compressor cooling under thermostat hysteresis
  SoC     battery state of charge, driven by PV generation against compressor and
          parasitic load, floored at the usable depth of discharge

The coupling between them is the point. When SoC hits the floor the compressor cannot run
regardless of what the thermostat asks, the box warms, and produce spoils. A monthly-mean
model cannot represent that failure at all; it is the failure that actually kills off-grid
cold storage in the field.

Outputs are the metrics an operator would be held to: hours in band, longest excursion,
brownout hours, minimum state of charge, and the worst week of the year.
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

HERE: Final[Path] = Path(__file__).parent
HOURLY: Final[Path] = HERE / "data" / "hourly" / "ho_2024.json"
OUT_DIR: Final[Path] = HERE / "out"

FILL_VALUE: Final[float] = -999.0
SUBSTEPS_PER_HOUR: Final[int] = 12          # five-minute integration
DT_S: Final[float] = 3600.0 / SUBSTEPS_PER_HOUR

# --- thermal mass -----------------------------------------------------------------
USABLE_M3: Final[float] = 1.0
CRATES: Final[int] = 40
CRATE_KG: Final[float] = 10.0
UTILISATION: Final[float] = 0.55
PRODUCE_CP: Final[float] = 3900.0           # J/kg/K, high-moisture horticulture
STRUCTURE_KG: Final[float] = 60.0
STRUCTURE_CP: Final[float] = 900.0
AIR_KG: Final[float] = USABLE_M3 * 1.16
AIR_CP: Final[float] = 1006.0

# --- envelope ---------------------------------------------------------------------
UA_W_PER_K: Final[float] = 2.0
SOLAR_ENVELOPE_GAIN: Final[float] = 0.04    # W per W/m2 of GHI, panel-shaded insulated box
DOOR_OPENINGS_PER_DAY: Final[int] = 24
MARKET_HOUR_START: Final[int] = 6
MARKET_HOUR_END: Final[int] = 18
AIR_EXCHANGE_FRACTION: Final[float] = 0.35
WATER_LATENT_J_PER_KG: Final[float] = 2.45e6
INTERNAL_RH: Final[float] = 0.90

# --- product ----------------------------------------------------------------------
LOADING_HOURS: Final[tuple[int, ...]] = (7, 11, 15)
CRATES_PER_LOADING: Final[float] = 2.0
FIELD_HEAT_OFFSET_K: Final[float] = 6.0
RESPIRATION_W_PER_TONNE_10C: Final[float] = 55.0
RESPIRATION_Q10: Final[float] = 2.4

# --- refrigeration ----------------------------------------------------------------
COMPRESSOR_W: Final[float] = 85.0           # electrical, fixed-speed hermetic
PARASITIC_W: Final[float] = 4.0             # evaporator fan, MCU, modem average
EVAP_APPROACH_K: Final[float] = 8.0
COND_APPROACH_K: Final[float] = 12.0
CARNOT_FRACTION: Final[float] = 0.341       # from the 4 degC / 35 degC datasheet point
HYSTERESIS_K: Final[float] = 1.0

# --- PV and battery ---------------------------------------------------------------
NOCT_C: Final[float] = 45.0
TEMP_COEFF: Final[float] = -0.0038
SOILING: Final[float] = 0.94                # annual average including Harmattan periods
MPPT_WIRING_EFF: Final[float] = 0.92
CHARGE_EFF: Final[float] = 0.97
USABLE_DOD: Final[float] = 0.85

# --- acceptance thresholds --------------------------------------------------------
CHILLING_FLOOR_C: Final[float] = 10.0       # solanaceous injury below this
QUALITY_CEILING_C: Final[float] = 15.0      # above this the hold stops being worth paying for


def _series(param: dict[str, float]) -> np.ndarray:
    """NASA POWER hourly dict -> array, with fill values carried forward."""
    keys = sorted(param.keys())
    vals = np.array([param[k] for k in keys], dtype=float)
    bad = vals <= FILL_VALUE + 1.0
    if bad.any():
        idx = np.where(~bad, np.arange(vals.size), 0)
        np.maximum.accumulate(idx, out=idx)
        vals = vals[idx]
    return vals


def saturation_pressure_pa(t_c: float) -> float:
    return 610.78 * np.exp(17.27 * t_c / (t_c + 237.3))


def humidity_ratio(t_c: float, rh: float) -> float:
    p_v = rh * saturation_pressure_pa(t_c)
    return 0.622 * p_v / (101_325.0 - p_v)


def cop(t_box: float, t_amb: float) -> float:
    """Instantaneous COP at the current box and ambient temperature."""
    t_evap = t_box - EVAP_APPROACH_K + 273.15
    t_cond = t_amb + COND_APPROACH_K + 273.15
    if t_cond <= t_evap + 1.0:
        return 8.0
    return max(CARNOT_FRACTION * (t_evap / (t_cond - t_evap)), 0.4)


@dataclass(slots=True)
class Result:
    """What the year did to the pod."""

    label: str
    array_w: float
    battery_kwh: float
    setpoint_c: float
    t_box: np.ndarray = field(repr=False)
    soc_frac: np.ndarray = field(repr=False)
    brownout_h: int = 0
    compressor_h: float = 0.0
    pv_kwh: float = 0.0

    @property
    def hours_in_band(self) -> float:
        ok = (self.t_box >= CHILLING_FLOOR_C) & (self.t_box <= QUALITY_CEILING_C)
        return 100.0 * ok.mean()

    @property
    def hours_above_ceiling(self) -> int:
        return int((self.t_box > QUALITY_CEILING_C).sum())

    @property
    def longest_excursion_h(self) -> int:
        over = self.t_box > QUALITY_CEILING_C
        best = run = 0
        for v in over:
            run = run + 1 if v else 0
            best = max(best, run)
        return best

    @property
    def min_soc_pct(self) -> float:
        return 100.0 * float(self.soc_frac.min())

    @property
    def peak_box_c(self) -> float:
        return float(self.t_box.max())

    @property
    def verdict(self) -> str:
        """Operational verdict.

        Total hours above the ceiling is the wrong test: a two-hour rise after 20 kg of
        field-hot produce lands is normal and harmless, and any real pod does it several
        times a week. What ruins a crate is a *sustained* excursion, so the verdict keys on
        the longest continuous excursion and the peak reached, with any battery brownout an
        automatic failure.
        """
        if self.brownout_h > 0:
            return "FAILS"
        if self.longest_excursion_h <= 6 and self.peak_box_c <= 18.0:
            return "HOLDS"
        if self.longest_excursion_h <= 24:
            return "MARGINAL"
        return "FAILS"


def simulate(ghi: np.ndarray, t_amb: np.ndarray, rh: np.ndarray, *,
             array_w: float, battery_kwh: float, setpoint_c: float,
             label: str, ua: float = UA_W_PER_K,
             openings_per_day: int = DOOR_OPENINGS_PER_DAY) -> Result:
    """Integrate the pod through the year."""
    n = ghi.size
    produce_kg = CRATES * CRATE_KG * UTILISATION
    capacitance = (produce_kg * PRODUCE_CP + STRUCTURE_KG * STRUCTURE_CP
                   + AIR_KG * AIR_CP)
    battery_wh = battery_kwh * 1000.0 * USABLE_DOD

    t_box = np.empty(n)
    soc_out = np.empty(n)
    temp = setpoint_c
    soc = battery_wh                      # start full
    brownout_h = 0
    compressor_s = 0.0
    pv_wh_total = 0.0
    compressor_on = False

    market_hours = MARKET_HOUR_END - MARKET_HOUR_START
    openings_per_market_hour = openings_per_day / market_hours

    for i in range(n):
        hour_of_day = i % 24
        g = ghi[i]
        amb = t_amb[i]
        rel_h = min(max(rh[i] / 100.0, 0.05), 1.0)

        # --- PV available this hour -------------------------------------------
        if g > 1.0:
            cell = amb + (NOCT_C - 20.0) / 800.0 * g
            derate = 1.0 + TEMP_COEFF * (cell - 25.0)
            pv_w = array_w * (g / 1000.0) * derate * SOILING * MPPT_WIRING_EFF
            pv_w = max(pv_w, 0.0)
        else:
            pv_w = 0.0
        pv_wh_total += pv_w

        # --- discrete loads applied once per hour ------------------------------
        infiltration_j = 0.0
        if MARKET_HOUR_START <= hour_of_day < MARKET_HOUR_END:
            exch_kg = (USABLE_M3 * AIR_EXCHANGE_FRACTION
                       * openings_per_market_hour * 1.16)
            sensible = exch_kg * AIR_CP * max(amb - temp, 0.0)
            w_out = humidity_ratio(amb, rel_h)
            w_in = humidity_ratio(temp, INTERNAL_RH)
            latent = exch_kg * max(w_out - w_in, 0.0) * WATER_LATENT_J_PER_KG
            infiltration_j = sensible + latent

        product_j = 0.0
        if hour_of_day in LOADING_HOURS:
            mass = CRATES_PER_LOADING * CRATE_KG
            product_j = mass * PRODUCE_CP * max(amb + FIELD_HEAT_OFFSET_K - temp, 0.0)

        # discrete energy dumped in at the top of the hour
        temp += (infiltration_j + product_j) / capacitance

        browned_this_hour = False
        for _ in range(SUBSTEPS_PER_HOUR):
            # thermostat with hysteresis
            if temp > setpoint_c + HYSTERESIS_K / 2.0:
                compressor_on = True
            elif temp < setpoint_c - HYSTERESIS_K / 2.0:
                compressor_on = False

            demand_w = PARASITIC_W + (COMPRESSOR_W if compressor_on else 0.0)
            net_w = pv_w - demand_w

            if net_w >= 0.0:
                soc = min(soc + net_w * CHARGE_EFF * DT_S / 3600.0, battery_wh)
                running = compressor_on
            else:
                need_wh = -net_w * DT_S / 3600.0
                if soc >= need_wh:
                    soc -= need_wh
                    running = compressor_on
                else:
                    # battery empty: compressor is shed, parasitics ride the PV
                    running = False
                    soc = max(soc - PARASITIC_W * DT_S / 3600.0, 0.0)
                    if compressor_on:
                        browned_this_hour = True
                        compressor_on = False

            q_cool = COMPRESSOR_W * cop(temp, amb) if running else 0.0
            if running:
                compressor_s += DT_S

            resp_w = ((produce_kg / 1000.0) * RESPIRATION_W_PER_TONNE_10C
                      * RESPIRATION_Q10 ** ((temp - 10.0) / 10.0))
            q_in = ua * (amb - temp) + SOLAR_ENVELOPE_GAIN * g + resp_w
            temp += (q_in - q_cool) * DT_S / capacitance

        if browned_this_hour:
            brownout_h += 1
        t_box[i] = temp
        soc_out[i] = soc / battery_wh

    return Result(label=label, array_w=array_w, battery_kwh=battery_kwh,
                  setpoint_c=setpoint_c, t_box=t_box, soc_frac=soc_out,
                  brownout_h=brownout_h, compressor_h=compressor_s / 3600.0,
                  pv_kwh=pv_wh_total / 1000.0)


def load_weather() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    raw = json.loads(HOURLY.read_text())["properties"]["parameter"]
    return (_series(raw["ALLSKY_SFC_SW_DWN"]), _series(raw["T2M"]), _series(raw["RH2M"]))


def _report(r: Result) -> None:
    logger.info("%-34s %6.0f W %5.1f kWh %5.1f C | %-8s in-band %5.1f%% | "
                "over-ceiling %4d h | longest %3d h | brownout %4d h | min SoC %4.0f%% | "
                "peak box %4.1f C | duty %4.1f%%",
                r.label, r.array_w, r.battery_kwh, r.setpoint_c, r.verdict,
                r.hours_in_band, r.hours_above_ceiling, r.longest_excursion_h,
                r.brownout_h, r.min_soc_pct, r.peak_box_c,
                100.0 * r.compressor_h / r.t_box.size)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    ghi, amb, rh = load_weather()
    logger.info("Ho, Volta Region — NASA POWER hourly, 2024 (%d hours)", ghi.size)
    logger.info("ambient %.1f to %.1f degC, mean %.1f | GHI peak %.0f W/m2, %.2f kWh/m2/day",
                amb.min(), amb.max(), amb.mean(), ghi.max(), ghi.sum() / 366 / 1000)
    logger.info("")
    logger.info("=" * 170)
    logger.info("DYNAMIC RESULT — thermostat, battery coupling, discrete door and loading events")
    logger.info("=" * 170)

    cases = [
        ("Kenya spec ported (330 W, 8 C)", 330.0, 4.8, 8.0),
        ("Kenya array, Ghana setpoint", 330.0, 4.8, 13.0),
        ("Ghana spec (450 W, 13 C)", 450.0, 4.8, 13.0),
        ("Ghana spec, heavy duty 40 doors", 450.0, 4.8, 13.0),
        ("HG-C production SKU (440 W)", 440.0, 4.8, 13.0),
    ]
    results = []
    for i, (label, aw, bk, sp) in enumerate(cases):
        openings = 40 if "heavy duty" in label else DOOR_OPENINGS_PER_DAY
        r = simulate(ghi, amb, rh, array_w=aw, battery_kwh=bk, setpoint_c=sp,
                     label=label, openings_per_day=openings)
        results.append(r)
        _report(r)

    logger.info("")
    logger.info("=" * 170)
    logger.info("WORST WEEK — the seven consecutive days with the lowest minimum state of charge")
    logger.info("=" * 170)
    for r in results:
        soc = r.soc_frac
        weekly = np.array([soc[i:i + 168].min() for i in range(0, soc.size - 168, 24)])
        w = int(weekly.argmin())
        start = w * 24
        seg_t = r.t_box[start:start + 168]
        logger.info("%-34s worst week starts day %3d | min SoC %4.0f%% | box %4.1f to %4.1f C",
                    r.label, w, 100 * weekly[w], seg_t.min(), seg_t.max())

    payload = {
        "site": "Ho, Volta Region, Ghana",
        "weather": "NASA POWER hourly, 2024, 8784 h",
        "timestep_min": 60 / SUBSTEPS_PER_HOUR,
        "cases": [
            {"label": r.label, "array_w": r.array_w, "battery_kwh": r.battery_kwh,
             "setpoint_c": r.setpoint_c, "verdict": r.verdict,
             "hours_in_band_pct": round(r.hours_in_band, 2),
             "hours_above_ceiling": r.hours_above_ceiling,
             "longest_excursion_h": r.longest_excursion_h,
             "brownout_h": r.brownout_h, "min_soc_pct": round(r.min_soc_pct, 1),
             "peak_box_c": round(r.peak_box_c, 2),
             "compressor_duty_pct": round(100 * r.compressor_h / r.t_box.size, 1),
             "pv_kwh_year": round(r.pv_kwh, 1)}
            for r in results
        ],
    }
    (OUT_DIR / "ho_dynamic_results.json").write_text(json.dumps(payload, indent=2))
    np.save(OUT_DIR / "ho_dynamic_traces.npy",
            np.vstack([r.t_box for r in results] + [r.soc_frac for r in results]))
    logger.info("")
    logger.info("wrote %s", OUT_DIR / "ho_dynamic_results.json")


if __name__ == "__main__":
    main()
