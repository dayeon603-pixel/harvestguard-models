# HarvestGuard — models and trust rail

Open engineering models for solar-powered cold storage serving smallholder horticulture in
sub-Saharan Africa, and the payment-and-record layer that runs on top of them.

Everything here is reproducible offline: climatology is cached, so the same input gives the
same number on any machine.

> **Status.** These are design and validation models. **No physical pod has been built.**
> Every figure below is what the physics says should happen; none of it is a field measurement.
> Each write-up ends with an explicit boundaries section stating what its model cannot support.

---

## `sim/` — the engineering models

| Module | What it does |
|---|---|
| `ghana_energy_model.py` | Steady-state monthly energy balance: PV supply against conduction, infiltration, produce pull-down and respiration, with a condition-dependent COP |
| `ghana_sensitivity.py` | Swings eight assumptions independently and reports whether the conclusion survives |
| `ho_dynamic_sim.py` | 8,784-hour dynamic run on real hourly weather, integrating box temperature and battery state of charge as coupled states |
| `pod_thermal_3d.py` | 8,925-cell finite-volume model of the pod interior, backward Euler on a prefactorised sparse operator |
| `pod_loading_policy.py` | How much field-hot produce the pod can absorb per cycle |
| `africa_sizing_engine.py` | Sizes array and battery for any site, then partitions 33 regions into a minimal SKU set by exact dynamic program |
| `ghana_circularity.py` | Material loop and emissions, with an internal consistency check |
| `fetch_africa_sites.py` | Fetches and caches NASA POWER climatology |
| `africa_atlas.py`, `pod_thermal_figure.py` | Figures |

**Write-ups:** `GHANA_SITE_TRANSFER.md`, `DYNAMIC_VALIDATION.md`, `THERMAL_3D.md`, `AFRICA_DEPLOYMENT.md`

### Three findings worth reading

**A pod sized for one African site fails at another.** Moved unchanged from an East African
highland baseline to Ho, Volta Region, the design runs an energy deficit in all twelve months —
worst case −442 Wh/day. Ho has 21% less solar resource against a 9.1 K hotter mean, so supply
falls and demand rises together. Eight assumptions swung independently; **0 of 8 flip it.**

**Stratification sets the setpoint, not the average.** The 3D interior model finds a 2.47 K spread
from the evaporator to the floor. At a 13 °C setpoint the coldest crate sits 0.85 K above the
chilling-injury floor; at 11 °C, **10 of 36 crates would be damaged while the thermostat still
reads acceptable.** A lumped model sees only the average and would permit the lower setpoint.

**One design cannot travel.** Across 33 regions in 20 countries the required array spans 120 W to
440 W — a 3.7× range. An exact dynamic program partitions that into three configurations covering
every region.

## `services/ledger/` — the trust rail

Signed pod events, a tamper-evident ledger, mobile-money settlement, and the farmer credit record
built from them. **28 passing tests.**

Three properties every record carries, because a lender needs all three:

| Property | Mechanism |
|---|---|
| A specific pod produced it | per-device HMAC-SHA256 over a canonical form |
| It has not been altered since | SHA-256 hash chain, each record committing to the previous |
| The money it claims moved actually moved | settlement callback confirmed before the record counts |

The credit profile is **not** a regulated credit score, does not predict default, and has never
been validated against repayment outcomes, because no loans have been written against it.

## Running it

All paths are relative to the repo root, so `cd` into the clone first.

```bash
pip install -r requirements.txt

python3 sim/ghana_energy_model.py        # steady-state balance
python3 sim/ho_dynamic_sim.py            # 8,784-hour dynamic run
python3 sim/pod_thermal_3d.py            # 3D interior model
python3 sim/africa_sizing_engine.py      # 33 regions, SKU partition

(cd services/ledger && python3 -m pytest -q)                    # 28 tests
(cd services/ledger && PYTHONPATH=src python3 demo_season.py)   # six months end to end
```

The demo runs a season of traffic through the live API, then tampers with a historical record to
show the chain detecting it and the service refusing to issue credit against unverified history.

## Data

NASA POWER (NASA Langley Research Center) — monthly climatology for 33 sites and full-year 2024
hourly data for Ho, cached under `sim/data/`. Country geometry from Natural Earth.

## Licence

MIT. The sizing engine is offered as an open method: if it is useful for siting cold storage
anywhere, use it.
