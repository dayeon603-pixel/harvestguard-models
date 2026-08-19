# HarvestGuard Trust Rail

The pod earns the daily revenue. This service is the part that compounds: signed pod events,
a tamper-evident ledger, mobile-money settlement, and the farmer credit record built on them.

## Why it exists

The product claim is that every stored crate becomes a verified record a lender can act on.
That claim only holds if "verified" survives contact with a bank. Three properties have to be
true of every record:

| Property | Mechanism |
|---|---|
| A specific pod produced it, not someone else | per-device HMAC-SHA256 over a canonical form |
| It has not been altered since | SHA-256 hash chain, each record committing to the previous |
| The money it claims moved actually moved | MTN MoMo settlement callback, confirmed before the record counts |

A signature alone lets an operator silently delete inconvenient records. A chain alone lets
anyone write records nobody observed. Payment reconciliation alone proves nothing about who
stored what. The rail needs all three.

## Run it

```bash
python3 -m pytest -q            # 28 tests
PYTHONPATH=src python3 demo_season.py   # six months of traffic through the live API
uvicorn hgledger.api:app --reload       # serve it
```

## What the demo shows

Six months of interleaved traffic from four farmers at `HG-GH-VOLTA-001`, over the real HTTP
API: 78 signed deposits, per-crate-day billing at GHS 2.16, MoMo settlement with a realistic
failure rate, and the credit profiles that fall out. Then it tampers with a historical record
to inflate a farmer's stored volume, and the chain catches it — after which the service
**refuses to issue a credit profile at all**. That refusal is the product.

## What this is not

The credit profile is not a regulated credit score, it does not predict default, and it has
never been validated against repayment outcomes because no loans have been written against it.
It is a thin-file evidence pack that gives a lender something to underwrite where today there
is nothing. Scoring is transparent and monotone rather than learned: with zero default
outcomes to train on, a fitted model would encode our own assumptions while looking
authoritative.

`SimulatedCollections` is a deterministic test double for MTN MoMo Collections. The state
machine it drives is the real one; the network integration is not built, and a merchant
account has not been onboarded.

## Layout

```
src/hgledger/
  models.py    domain types; a record failing any guarantee cannot be constructed
  crypto.py    device signing and the chain commitment
  ledger.py    append-only chain, replay and out-of-order rejection
  billing.py   per-crate-day pricing and the session state machine
  momo.py      MTN MoMo Collections adapter + deterministic simulator
  credit.py    profile derived only from chain-verified, settled sessions
  api.py       HTTP surface
tests/         28 tests, targeting the guarantees rather than the happy path
```
