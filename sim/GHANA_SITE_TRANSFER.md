# Site transfer: Meru (Kenya) → Ho (Volta Region, Ghana)

**Date:** 2026-08-18 · **Model:** `sim/ghana_energy_model.py`, `sim/ghana_sensitivity.py`
**Climate input:** NASA POWER monthly climatology, Ho 6.601 °N 0.471 °E (elev. 114 m) and Meru 0.05 °N 37.65 °E
**Result:** the v1.2 pod as specified **does not close its energy budget at Ho**. A Ghana configuration is derived below.

---

## 1. Why the site transfer is not free

The v1.2 hardware spec sizes the array and battery against Meru, Kenya. Ho is a different climate class.

| | Meru, Kenya | Ho, Volta, Ghana | Δ |
|---|---|---|---|
| Annual GHI (kWh/m²/day) | 6.19 | 4.91 | **−21%** |
| Worst month GHI | 5.57 (Jul) | 4.31 (Aug) | −23% |
| Annual mean air temp | 17.6 °C | 26.7 °C | **+9.1 K** |
| Annual mean RH | 73.4% | 79.3% | +5.9 pp |
| Wet-season RH peak | 78% (May) | 87% (Jun) | +9 pp |
| Dust season | none | Harmattan, Dec–Feb | soiling loss |

Less sun, hotter ambient, wetter air. Supply falls and demand rises at the same time, and the two compound.

## 2. Energy balance at Ho with the hardware unchanged

330 W array, 4.8 kWh LiFePO₄, 8 °C setpoint:

| Month | Supply (Wh/d) | Demand (Wh/d) | Margin |
|---|---|---|---|
| Jan | 1176 | 1503 | **−327** |
| Feb | 1174 | 1617 | **−442** |
| Mar | 1326 | 1576 | **−250** |
| Apr | 1356 | 1531 | **−175** |
| May | 1297 | 1454 | **−156** |
| Jun | 1176 | 1341 | **−165** |
| Jul | 1141 | 1283 | **−142** |
| Aug | 1116 | 1290 | **−174** |
| Sep | 1183 | 1335 | **−152** |
| Oct | 1305 | 1364 | **−59** |
| Nov | 1332 | 1436 | **−104** |
| Dec | 1182 | 1490 | **−309** |

**Deficit in all twelve months.** Worst month February at −442 Wh/day, 27% short of demand. The same
configuration at Meru carries a +757 Wh/day worst-month surplus, so the design is sound where it was sized
and simply out of its envelope at Ho.

Dominant load terms at Ho in the worst month (thermal Wh/day): produce pull-down 1745, conduction 1000,
respiration 244, infiltration latent 103, infiltration sensible 68. Pull-down dominates because field-hot
produce arrives at roughly ambient +6 K, and Ho's ambient is 9 K above Meru's.

## 3. The Ghana configuration

Two changes, one operational and one on the bill of materials.

**(a) Setpoint 13 °C, not 8 °C.** Ghana's high-loss perishables are tomato, pepper and garden egg, all
solanaceous, all subject to chilling injury below ~10 °C (USDA postharvest handbook). Running the warm end of
the 4–13 °C band is what the crop requires, and it also cuts the load: worst-month demand falls from 1617 to
1229 Wh/day and the COP rises from 2.08 to 2.38. The setpoint that is correct horticulturally is also the
setpoint that is correct energetically.

**(b) Array 450 W, not 330 W.**

| Configuration | Worst month | Margin | Verdict |
|---|---|---|---|
| Meru baseline, as built (330 W, 8 °C) | Nov | +757 Wh/d | closes |
| Ho, hardware unchanged (330 W, 8 °C) | Feb | −442 Wh/d | **deficit** |
| Ho, setpoint change only (330 W, 13 °C) | Feb | −55 Wh/d | **deficit** |
| Ho, 13 °C + 400 W | Feb | +194 Wh/d | closes nominally, fails stacked-pessimistic |
| **Ho, 13 °C + 450 W** | **Feb** | **+372 Wh/d** | **closes, and survives stacked-pessimistic at +90** |

Minimum array to close every month at Ho at 13 °C is 350 W. 450 W is specified because it is the smallest
size that also holds margin when four assumptions are pushed against the design simultaneously (§4).

**Ghana pod summary:** 450 W array · 4.8 kWh LiFePO₄ unchanged · 13 °C setpoint for solanaceous cohorts ·
worst-month demand 1229 Wh/day · **80 h no-sun autonomy** (not the 89 h quoted for Meru) · COP 2.38 at the
February design point. Incremental BOM cost over the 330 W array is roughly USD 35–45 at pilot quantity.

## 4. Robustness

Each assumption was swung independently across a defensible range and the worst-month margin recomputed for
the unchanged hardware at Ho.

| Assumption swung | Low → margin | High → margin | Flips conclusion? |
|---|---|---|---|
| Envelope UA, 1.5–3.0 W/K | −322 | −683 | no |
| Door openings, 12–40 /day | −401 | −497 | no |
| Crates loaded, 3–10 /day | −23 | −1002 | no |
| Harmattan soiling, 0.82–0.95 | −547 | −377 | no |
| PV temp coefficient, −0.0045 to −0.0030 /K | −468 | −414 | no |
| Condenser approach, 8–16 K | −307 | −578 | no |
| Battery round-trip, 0.90–0.98 | −504 | −405 | no |
| Field-heat entry offset, 3–9 K | −349 | −536 | no |

**0 of 8 swings flip the result.** The margin stays negative across every single-assumption range tested, so
the conclusion is a property of the site rather than of a chosen parameter.

## 5. Boundaries of the claim

This is a steady-state monthly-mean energy balance built on NASA POWER climatology. It sizes equipment and
determines whether a configuration closes; it does not predict the temperature trace of a particular day.
Latent load is computed from psychrometrics at monthly-mean relative humidity, which understates a wet-season
rain event. The COP model is a Carnot fraction of 0.341 back-calculated from the datasheet's 4 °C / 35 °C
operating point and held constant across conditions. Field measurement at the pilot site is what converts
these figures from sized to verified, and instrumenting that is a Phase-1 pilot deliverable.

## 6. What changes downstream

- `hardware_spec.md`: add a Ghana variant row, 450 W array, 13 °C default setpoint, 80 h autonomy.
- `datasheet/`: the current sheet quotes Meru figures (1340 Wh/day yield, 89 h autonomy). A Ghana sheet
  should quote 450 W, 1229 Wh/day worst-month demand, 80 h.
- Deck slide 4: the energy-budget figure is `sim/out/ghana_energy_balance.png`.
- Application: the pilot is specified for Volta with named numbers rather than inherited East African ones.
