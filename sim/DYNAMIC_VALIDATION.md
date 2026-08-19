# Dynamic validation at Ho: 8,784 hours of real weather

**Date:** 2026-08-18 · **Model:** `sim/ho_dynamic_sim.py` · **Figure:** `sim/out/ho_worst_week.png`
**Weather:** NASA POWER hourly, Ho (6.601 °N, 0.471 °E), full year 2024, 8,784 records
**Integration:** five-minute sub-steps, two coupled state variables

---

## 1. Why a second model

The monthly-mean balance in `ghana_energy_model.py` answers a sizing question: does average supply cover
average demand. It cannot answer the question a farmer is actually paying to have answered, which is
whether the box holds temperature through the worst week of the year.

This model integrates two coupled states hour by hour:

- **T_box** — driven by conduction, solar gain on the envelope, discrete door openings during market
  hours, field-hot produce arriving at three loading times a day, respiration, and compressor cooling
  under thermostat hysteresis.
- **SoC** — driven by PV generation against compressor and parasitic load, floored at usable depth of
  discharge.

The coupling is the whole point. **When state of charge hits the floor the compressor is shed regardless
of what the thermostat asks**, the box warms, and produce spoils. A monthly-mean model cannot represent
that failure mode at all, and it is the failure mode that actually kills off-grid cold storage in the field.

Thermal mass is dominated by the produce itself: 220 kg at 55% utilisation gives roughly 913 kJ/K, so
the box has a long time constant and buffers short interruptions. That buffering is why the monthly model
and this one disagree in an instructive way.

## 2. Results, full year 2024

| Configuration | Verdict | In band | Longest excursion | Brownout hours | Min SoC | Peak box | Duty |
|---|---|---|---|---|---|---|---|
| Kenya spec ported, 330 W / 8 °C | **FAILS** | 59.7% | **171 h** | **3,383** | **0%** | **24.0 °C** | 55.9% |
| Kenya array, Ghana setpoint, 330 W / 13 °C | HOLDS | 98.9% | 3 h | 0 | 30% | 16.2 °C | 51.9% |
| **Ghana spec, 450 W / 13 °C** | **HOLDS** | **98.9%** | **3 h** | **0** | **76%** | **16.2 °C** | 51.9% |
| Ghana spec, heavy duty (40 door openings/day) | HOLDS | 98.6% | 3 h | 0 | 75% | 16.3 °C | 53.0% |
| HG-C production SKU, 440 W / 13 °C | HOLDS | 98.9% | 3 h | 0 | 76% | 16.2 °C | 51.9% |

The ported Kenya specification is not marginal at Ho. It browns out for **3,383 hours, 39% of the year**,
spends 1,167 hours above the quality ceiling, and its longest single excursion is **171 continuous hours**
peaking at 24 °C. Every crate inside is lost. The failure week is plotted in `out/ho_worst_week.png`:
the battery sits pinned at zero for seven straight days while the box drifts between 15 and 21 °C.

## 3. An honest refinement to the earlier claim

The monthly-mean model said the Ghana fix required both a 13 °C setpoint **and** a 450 W array. The dynamic
model is more precise, and slightly less flattering to the array upgrade:

**The setpoint change does most of the work.** At 330 W and 13 °C the pod already holds: zero brownouts,
three-hour longest excursion, 98.9% of hours in band. Raising the setpoint from 8 °C to 13 °C cuts the
lift, raises COP from 2.08 to 2.38, and is what the crop requires anyway.

**The array upgrade buys reserve, not basic function.** Going 330 W → 450 W moves minimum state of charge
from **30% to 76%**. In a typical year both configurations hold. The 450 W is justified by what 30%
minimum SoC means in practice: no headroom for panel soiling beyond modelled, cell degradation over a
ten-year life, a worse-than-2024 wet season, or utilisation above 55%. Specifying to a 30% floor is
specifying a pod that fails in year four.

That is a real correction to how the number should be presented. The array is a **reliability margin
decision**, not a does-it-work decision, and saying so is more defensible than implying the pod cannot
run at 330 W.

## 4. What the model reproduces that averages cannot

- **Loading spikes.** Twenty kilograms of field-hot produce entering a 913 kJ/K box lifts it about 1.7 K;
  with infiltration the observed spike is 2–3 K above the thermostat band, three times a day. This is why
  peak box temperature is 16.2 °C against a 13 °C setpoint, and why 96 hours a year sit above 15 °C in a
  perfectly healthy pod.
- **Why total hours above the ceiling is the wrong metric.** Those 96 hours are ninety-six separate
  two-to-three-hour rises after loading, not a spoilage event. The verdict logic therefore keys on
  *longest continuous excursion* and peak, with any brownout an automatic failure. Judging by total hours
  would have marked a healthy pod as failing.
- **Duty cycle.** The compressor runs 52% of the time at Ho, against the 50% assumed in the v1.2 hardware
  spec. That assumption survives.

## 5. Boundaries of the claim

Single year (2024) at one site; a multi-year run would bound inter-annual variability and is the obvious
next step. Weather is NASA POWER reanalysis, not a ground station at the pilot site. Door openings and
loading times are a modelled market pattern, not observed behaviour, and are the least defensible inputs
here: the heavy-duty case at 40 openings a day is included precisely because that input is unmeasured.
The compressor is modelled as fixed-speed drawing constant electrical power with COP varying by
condition, which is right for a BD35F-class hermetic but ignores start-up transients and defrost. No
degradation of battery or panel over life is modelled, which is exactly why the reserve margin in §3
matters. Field measurement at the pilot is what closes all of this.
