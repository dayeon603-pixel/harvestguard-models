# Continental deployment: one sizing engine, three pods, 33 regions

**Date:** 2026-08-18
**Models:** `sim/fetch_africa_sites.py`, `sim/africa_sizing_engine.py`, `sim/africa_atlas.py`
**Data:** NASA POWER monthly climatology, 33 sites, cached in `sim/data/africa/`
**Figure:** `sim/out/africa_deployment_atlas.png` · **Interactive:** Africa Cold Chain Atlas

---

## 1. The finding that forces a system

The Ghana site transfer showed that a pod sized for Meru fails at Ho. That is not a Ghana problem. It is
the general case, and it is the reason a single-SKU cold-storage product cannot scale across Africa.

Across 33 smallholder horticulture regions the design inputs span:

| | Minimum | Maximum | Ratio |
|---|---|---|---|
| Annual solar resource | 4.48 kWh/m²/day (Yaoundé) | 6.74 (Hargeisa) | 1.50× |
| Mean ambient temperature | 15.6 °C (Nakuru) | 28.7 °C (Niamey) | +13.1 K |
| **Peak daily electrical demand** | **482 Wh (Nakuru)** | **1,662 Wh (Niamey)** | **3.4×** |
| **Array required at 13 °C** | **120 W (Nakuru)** | **440 W (Niamey)** | **3.7×** |

Supply and demand move in opposite directions across the continent and they are not correlated in the
helpful direction. The Sahel is bright but brutally hot; the humid forest belt is both dim and hot; the East
African and Ethiopian highlands are bright and cool. A pod that closes in Addis Ababa on 140 W browns out
in Niamey, and a pod built for Niamey wastes $152 of hardware in Nakuru.

## 2. What the engine does

For every site, `africa_sizing_engine.py` solves a two-variable problem, array watts and battery kWh,
against two hard constraints:

1. the monthly energy balance closes in **all twelve months**, and
2. no-sun autonomy clears **48 hours** in the worst month.

Battery is solved first, since autonomy is a floor set by demand and not by the array. The array is then
stepped up until the balance closes, and stepped further until it still closes under a **stacked pessimistic
case** applied simultaneously: envelope UA degraded to 2.5 W/K, 32 door openings a day, soiling at 0.85/0.90,
and a 15 K condenser approach. The recommendation is the first size that survives all of it, so no
deployment sits on a knife edge.

Sizing runs at the setpoint the crop requires. The default programme is solanaceous, 13 °C, because tomato,
pepper and garden egg dominate smallholder perishable loss across the continent and suffer chilling injury
below about 10 °C.

## 3. The product line

Shipping 33 bespoke designs is not a business. The per-site requirements are partitioned into SKU tiers by
an exact dynamic program that minimises total oversizing cost, since a tier must be at least as large as
every site it serves.

| SKUs | Array tiers (W) | Wasted per pod | 
|---|---|---|
| 1 | 440 | $23.80 |
| 2 | 260, 440 | $10.87 |
| **3** | **200, 300, 440** | **$6.27** |
| 4 | 200, 300, 400, 440 | $4.55 |
| 5 | 150, 200, 300, 400, 440 | $3.35 |

Three tiers is the knee. Going to four saves $1.72 a pod and adds a whole manufacturing line.

| SKU | Array | Battery | Cost at volume | Regions | Climate served |
|---|---|---|---|---|---|
| **HG-A** | 200 W | 2.4 kWh | $451 | 10 | East African and Ethiopian highlands, southern plateau |
| **HG-B** | 300 W | 3.6 kWh | $524 | 9 | mid-altitude savanna, lake basin, semi-arid north |
| **HG-C** | 440 W | 4.8 kWh | $603 | 14 | humid lowland and forest belt, Sahel |

Three SKUs cover all 33 regions at a mean pod cost of $535 against $495 for perfectly bespoke sizing: an
**8.1% premium for manufacturability**. Note where that premium comes from. Array oversizing is only $6.27
of it; the rest is battery tiering, because batteries come in coarse steps and cost $47.9/kWh against $0.158/W
for PV. If the product line is ever to be trimmed further, the battery ladder is the place to do it, not
the array.

**Ghana sits in HG-C.** Ho requires 430 W, and the Volta pilot therefore ships the largest of the three
configurations. That is consistent with the standalone Ghana analysis, which specified 450 W on a slightly
more conservative soiling assumption.

## 4. What this changes about the business

A competitor ships a cold room. This ships a **sizing engine plus three pods**, which means:

- **Any new country is a data lookup, not a redesign.** A deployment in Nampula or Kano is specified from
  cached climatology in seconds, with the SKU falling out of the model.
- **Capital is not wasted at the easy end.** Sizing everything at the continental maximum would overspend
  $152 per pod in the highlands, which at scale is the difference between a fundable unit economic and a
  broken one.
- **The failure mode is designed out.** The common way off-grid cold storage dies in the field is a system
  sized for a brochure climate that browns out in the real one. Every configuration here is verified against
  its own site's worst month under stress.

## 5. What the engine does not yet do

The cool programme (6 °C, brassicas and temperate highland vegetables) is feasible at all 33 sites but
raises the median array from 300 W to 440 W, which would change the SKU partition; it is modelled but not
costed into the product line. Sizing uses monthly-mean climatology, so it dimensions equipment rather than
predicting a specific day. Crop mix per region is assumed from agro-ecology rather than measured. Site
selection is by horticultural density and cold-chain absence, not by market-entry sequencing, so the atlas
says what each region needs and not which to enter first. Field measurement at the Volta pilot is what
calibrates the whole engine, and it is the reason the pilot is instrumented.
