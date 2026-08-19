# Inside the pod: a 3D thermal model, and what it changed

**Date:** 2026-08-19 · **Model:** `sim/pod_thermal_3d.py` · **Figure:** `sim/out/pod_thermal_3d.png`
**Method:** finite-volume transient conduction, 8,925 cells at 50 mm, backward Euler on a prefactorised
sparse operator · **Conditions:** Ho, Volta Region, February ambient 28.8 °C

---

## 1. Why a lumped model is not enough

The steady-state and hourly models both treat the pod as a single temperature. That is enough to size
an array. It cannot answer the two questions that decide whether the rated capacity is real:

- **Where does the cold actually sit?** A top-mounted evaporator does not cool 36 crates equally.
- **Can the coldest crate be damaged while the average looks fine?** Solanaceous crops suffer chilling
  injury below about 10 °C. A box averaging 13 °C can still be injuring produce, and a thermostat
  reading one point will never show it.

This model resolves the interior: crates as distinct solid regions, air channels between them, the
evaporator as a capacity-limited cold face over part of the ceiling, and respiration distributed
through the produce.

## 2. The headline result

At a 13 °C setpoint in steady state, crate mean temperatures by stack level:

| Level | Temperatures (°C) |
|---|---|
| 4 (top, nearest the coil) | 11.7 · 11.3 · 11.7 ‖ 11.5 · **10.9** ‖ 11.7 · 11.3 · 11.7 |
| 3 | 12.4 · 12.2 · 12.4 ‖ 12.3 · 12.0 ‖ 12.4 · 12.2 · 12.4 |
| 2 | 12.9 · 12.8 · 12.9 ‖ 12.9 · 12.7 ‖ 12.9 · 12.8 · 12.9 |
| 1 (floor) | 13.3 · 13.2 · 13.3 ‖ 13.3 · 13.2 ‖ 13.3 · 13.2 · 13.3 |

**Spread is 2.47 K, top to bottom.** Air sits at 11.75 °C, the coldest crate at **10.85 °C**, which is
**0.85 K above the chilling-injury floor**. All 36 crates are inside the usable band, but the margin at
the top of the stack is thin.

## 3. What this changes: the setpoint has a floor

The obvious energy saving is to lower the setpoint. The 3D model says that is exactly wrong.

| Setpoint | Coldest crate | Spread | Crates injured |
|---|---|---|---|
| **13 °C** | 10.85 °C | 2.47 K | **0** |
| 12 °C | 9.80 °C | 2.55 K | **1** |
| 11 °C | 8.74 °C | 2.62 K | **10** |
| 10 °C | 7.69 °C | 2.69 K | **27 of 36** |

A lumped model sees only the average and would permit 11 °C without complaint. The 3D model shows
that setting produces chilling injury in ten crates while the thermostat reads a perfectly acceptable
number.

This matters beyond the energy question: **the 13 °C setpoint is now justified twice, independently.**
Horticulturally, because tomato and pepper are damaged below ~10 °C. Thermally, because a 2.47 K spread
means 13 °C is the lowest setpoint that keeps the whole stack above that floor. The two arguments were
derived separately and agree.

## 4. Field-hot loading

Six crates arriving at 32 °C into a cold pod:

- All six reach the 15 °C quality band in **7.0 to 8.5 hours**.
- Six of thirty neighbouring crates are briefly pushed above 15 °C, and **all recover fully**; by hour
  12 the whole stack is back inside the band.
- The rate limit is **conduction, not compressor capacity**. A crate of bulk produce has a thermal time
  constant near 16 hours, so the coil cannot reach a crate core quickly no matter how cold the air is.

The practical consequence is that the pod is a **holding** device rather than a pre-cooling device, and
overnight is the right billing unit — which is what the economics already assumed with a four-day hold.

## 5. Two modelling errors caught, and what they cost

Both were mine, and both produced confident, wrong answers before being found. Recording them because
the corrections are the reason the result above can be trusted.

**Unresolved air channels.** The first geometry gave crates a 15–25 mm gap while the grid used 50 mm
cells. The channels therefore did not exist numerically and the stack behaved as one solid block, which
produced a false result in which the pod could never reach setpoint. Fixing it required shrinking the
crate footprint to 0.34 × 0.22 m so the channels are 55 mm and 45 mm — at least one cell wide. **That is
now a real design requirement, not a modelling convenience:** without channelled air, the interior of a
densely stacked pod is unreachable by the coil.

**Air treated as a diffusive medium.** Fan-driven circulation is advective. Representing it with a
conductivity near still air, or even 1.1 W/m·K, starves the stack of cooling and produced a false warm
drift in which the pod never held its setpoint. The value that reproduces the fan's actual thermal
capacity flow — roughly 0.5 m/s through 0.25 m² of channel, about 145 W/K — corresponds to an effective
conductivity near 25 W/m·K. Using that made explicit time-stepping unaffordable, which is why the solver
is backward Euler on a prefactorised sparse operator. Runtime fell from four minutes to eighteen seconds
and the physics became correct at the same time.

A third error was a false thermal runaway from extrapolating the Q10 respiration law from 10 °C to
45 °C, a 21× multiplier far outside its valid range. The exponent is now capped at 30 °C.

## 6. Boundaries of the claim

This is not CFD. There is no momentum equation and no resolved jet from the evaporator fan; circulation
is a calibrated effective conductivity, and that parameterisation is the model's principal weakness. The
absolute stratification spread should be read as indicative; the *mechanism* and the *direction* of the
setpoint-floor result are robust, because they follow from produce conductivity and geometry rather than
from the air model.

Grid resolution is 50 mm, which resolves the air channels at roughly one cell — adequate to represent
them, too coarse to resolve flow within them. Crate contents are modelled as a homogeneous bulk with a
single conductivity, so the model says nothing about a single fruit. Door openings are not included in
the steady-state case. No validation against a physical pod exists, because no physical pod exists;
instrumenting the Volta pilot with sensors at the top and bottom of the stack is what would confirm or
refute the 2.47 K spread, and that measurement is cheap and worth making first.
