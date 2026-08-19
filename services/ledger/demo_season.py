"""End-to-end demonstration: one season at the Volta pod, over the live HTTP API.

Runs six months of real traffic through the actual service — signed pod events, per-crate-day
billing, MTN MoMo settlement, and the credit profiles that fall out of it — then attempts to
tamper with history to show the chain catches it.

Run:  python3 demo_season.py
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from hgledger import CrateClass, EventType, PodEvent, new_device_key, sign
from hgledger.api import app, _billing, _ledger

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("demo")

POD = "HG-GH-VOLTA-001"
SEASON_START = datetime(2026, 2, 2, 6, 0, tzinfo=timezone.utc)
FARMERS = [
    ("FRM-AMA-001", "233241000001", 8, 5),    # anchor trader, heavy user
    ("FRM-KOFI-002", "233241000002", 5, 4),
    ("FRM-ESI-003", "233241000003", 3, 3),
    ("FRM-YAW-004", "233241000004", 2, 1),    # occasional, thin file
]


def main() -> None:
    client = TestClient(app)
    rng = random.Random(7)

    key = new_device_key()
    client.post(f"/pods/{POD}/register", params={"device_key": key})
    logger.info("registered pod %s", POD)
    logger.info("")

    # Build the whole season first and sort chronologically. A pod sees interleaved traffic
    # from every farmer at once, and the ledger's out-of-order guard correctly rejects a
    # stream that walks backwards in time.
    visits: list[tuple[datetime, str, str, int, int]] = []
    for farmer_id, msisdn, crates, visits_per_month in FARMERS:
        for month in range(6):
            for visit in range(visits_per_month):
                opened = SEASON_START + timedelta(days=30 * month + visit * 5,
                                                  hours=rng.randint(0, 6))
                hold_days = rng.choice([2, 3, 4, 4, 5, 7])
                n = max(1, crates + rng.randint(-1, 1))
                visits.append((opened, farmer_id, msisdn, n, hold_days))
    visits.sort(key=lambda v: v[0])

    settled = failed = 0
    for opened, farmer_id, msisdn, n, hold_days in visits:
        closed = opened + timedelta(days=hold_days)

        # 1. pod asserts the deposit, signed with its device key
        ev = PodEvent(pod_id=POD, farmer_id=farmer_id, event_type=EventType.DEPOSIT,
                      occurred_at=opened, crates=n, crate_class=CrateClass.GRADE_A,
                      box_temp_c=round(rng.uniform(12.4, 14.6), 2),
                      battery_soc=round(rng.uniform(0.74, 1.0), 3))
        r = client.post("/events", json={"event": ev.model_dump(mode="json"),
                                         "signature": sign(key, ev.canonical())})
        assert r.status_code == 201, r.text

        # 2. billing session
        r = client.post("/sessions", json={"pod_id": POD, "farmer_id": farmer_id,
                                           "crates": n, "opened_at": opened.isoformat()})
        session_id = r.json()["session_id"]

        # 3. close and request payment
        r = client.post(f"/sessions/{session_id}/close",
                        json={"closed_at": closed.isoformat(), "msisdn": msisdn})
        body = r.json()

        # 4. provider callback. One session in twenty is not completed by the farmer.
        ok = rng.random() > 0.06
        client.post("/payments/callback", json={
            "intent_id": body["intent_id"], "provider_reference": body["provider_reference"],
            "succeeded": ok, "amount_ghs": body["amount_ghs"] if ok else "0.00",
            "settled_at": (closed + timedelta(minutes=3)).isoformat()})
        settled += int(ok); failed += int(not ok)

        # 5. the door only opens on confirmed money
        d = client.get(f"/pods/{POD}/unlock", params={"session_id": session_id}).json()
        assert d["granted"] is ok, d

    logger.info("=" * 92)
    logger.info("SEASON COMPLETE — %d signed events, %d settled sessions, %d failed payments",
                len(_ledger), settled, failed)
    logger.info("=" * 92)
    h = client.get("/health").json()
    logger.info("chain head %s | verifies: %s", h["chain_head"][:24] + "...", h["chain_valid"])
    logger.info("")

    logger.info("%-14s %8s %11s %12s %8s %8s  %-12s %s",
                "farmer", "sessions", "crate-days", "paid GHS", "months", "score", "band", "lendable")
    logger.info("-" * 92)
    for farmer_id, *_ in FARMERS:
        p = client.get(f"/farmers/{farmer_id}/credit").json()
        logger.info("%-14s %8d %11d %12s %8d %8d  %-12s %s",
                    p["farmer_id"], p["sessions"], p["crate_days"], p["total_paid_ghs"],
                    p["distinct_months_active"], p["score"], p["band"],
                    "yes" if p["band"] in ("emerging", "established") else "no")

    logger.info("")
    logger.info("=" * 92)
    logger.info("TAMPER TEST — operator inflates a farmer's stored volume after the fact")
    logger.info("=" * 92)
    target = _ledger.records()[10]
    logger.info("record 10 before: %d crates for %s", target.event.crates, target.event.farmer_id)
    forged = target.model_copy(update={"event": target.event.model_copy(update={"crates": 40})})
    _ledger._records[10] = forged  # noqa: SLF001
    logger.info("record 10 after : %d crates", forged.event.crates)
    logger.info("chain verifies: %s", client.get("/health").json()["chain_valid"])
    r = client.get("/farmers/FRM-AMA-001/credit")
    logger.info("credit profile request -> HTTP %d: %s", r.status_code,
                r.json().get("detail", "")[:70])
    logger.info("")
    logger.info("The record was altered, the chain detected it, and the rail refused to issue")
    logger.info("a credit profile against unverified history. That refusal is the product.")


if __name__ == "__main__":
    main()
