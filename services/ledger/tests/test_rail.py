"""Tests for the HarvestGuard trust rail.

These target the properties a lender is relying on, not the happy path. The happy path is
one test; the rest are attempts to break the guarantees: forged signatures, replayed events,
retro-edited history, underpayment, and credit profiles issued against a broken chain.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hgledger import (
    BadSignature,
    BillingEngine,
    CrateClass,
    EventLedger,
    EventType,
    PodEvent,
    ReplayedEvent,
    SettlementState,
    SignedPodEvent,
    SimulatedCollections,
    UnknownPod,
    amount_for,
    build_profile,
    crate_days,
    new_device_key,
    sign,
)
from hgledger.momo import net_of_fees

POD = "HG-GH-VOLTA-001"
FARMER = "FRM-0001"
MSISDN = "233241234567"
T0 = datetime(2026, 3, 2, 7, 30, tzinfo=timezone.utc)


@pytest.fixture()
def key() -> str:
    return new_device_key()


@pytest.fixture()
def ledger(key: str) -> EventLedger:
    led = EventLedger()
    led.register_pod(POD, key)
    return led


def make_event(**kw) -> PodEvent:
    base = dict(pod_id=POD, farmer_id=FARMER, event_type=EventType.DEPOSIT,
                occurred_at=T0, crates=4, crate_class=CrateClass.GRADE_A,
                box_temp_c=13.1, battery_soc=0.94)
    base.update(kw)
    return PodEvent(**base)


def signed(event: PodEvent, key: str) -> SignedPodEvent:
    return SignedPodEvent(event=event, signature=sign(key, event.canonical()))


# --- ledger integrity -------------------------------------------------------------

def test_signed_event_is_accepted_and_chained(ledger: EventLedger, key: str) -> None:
    rec = ledger.append(signed(make_event(), key))
    assert rec.sequence == 0
    assert rec.prev_hash == "0" * 64
    assert ledger.verify_chain()
    assert ledger.head == rec.record_hash


def test_forged_signature_is_rejected(ledger: EventLedger) -> None:
    attacker_key = new_device_key()
    with pytest.raises(BadSignature):
        ledger.append(signed(make_event(), attacker_key))
    assert len(ledger) == 0


def test_unregistered_pod_is_rejected(ledger: EventLedger, key: str) -> None:
    with pytest.raises(UnknownPod):
        ledger.append(signed(make_event(pod_id="HG-GH-UNKNOWN"), key))


def test_replayed_event_is_rejected(ledger: EventLedger, key: str) -> None:
    ev = signed(make_event(), key)
    ledger.append(ev)
    with pytest.raises(ReplayedEvent):
        ledger.append(ev)
    assert len(ledger) == 1


def test_tampering_with_history_breaks_the_chain(ledger: EventLedger, key: str) -> None:
    """The core claim: a record cannot be altered after the fact without detection."""
    for i in range(4):
        ledger.append(signed(make_event(occurred_at=T0 + timedelta(days=i)), key))
    assert ledger.verify_chain()

    # Operator quietly inflates a farmer's stored volume.
    tampered = ledger.records()[1].model_copy(
        update={"event": ledger.records()[1].event.model_copy(update={"crates": 40})}
    )
    ledger._records[1] = tampered  # noqa: SLF001 — deliberately reaching in to forge

    assert ledger.verify_chain() is False


def test_chain_survives_many_events(ledger: EventLedger, key: str) -> None:
    for i in range(200):
        ledger.append(signed(make_event(occurred_at=T0 + timedelta(hours=i)), key))
    assert len(ledger) == 200
    assert ledger.verify_chain()


def test_canonical_form_is_signature_stable() -> None:
    ev = make_event()
    assert ev.canonical() == ev.canonical()
    assert ev.canonical() != make_event(crates=5).canonical()


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError):
        PodEvent(pod_id=POD, farmer_id=FARMER, event_type=EventType.TELEMETRY,
                 occurred_at=datetime(2026, 3, 2, 7, 30))


# --- billing ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("crates", "hours", "expected_days"),
    [(1, 1, 1), (1, 24, 1), (1, 25, 2), (4, 48, 2), (4, 49, 3), (10, 96, 4)],
)
def test_part_days_round_up(crates: int, hours: int, expected_days: int) -> None:
    assert crate_days(crates, T0, T0 + timedelta(hours=hours)) == crates * expected_days


def test_amount_matches_published_price() -> None:
    # 4 crates held 3 days at GHS 2.16 per crate-day
    assert amount_for(4, T0, T0 + timedelta(days=3)) == Decimal("25.92")


def test_close_before_open_is_an_error() -> None:
    with pytest.raises(ValueError):
        crate_days(1, T0, T0 - timedelta(hours=1))


def test_cannot_bill_an_open_session() -> None:
    eng = BillingEngine()
    s = eng.open_session(pod_id=POD, farmer_id=FARMER, crates=2, opened_at=T0)
    with pytest.raises(ValueError):
        eng.raise_intent(s.session_id, MSISDN)


def test_merchant_fee_is_borne_by_operator() -> None:
    assert net_of_fees(Decimal("100.00")) == Decimal("99.00")


# --- settlement ------------------------------------------------------------------

def _settled_session(eng: BillingEngine, opened: datetime, days: int, crates: int = 4):
    s = eng.open_session(pod_id=POD, farmer_id=FARMER, crates=crates, opened_at=opened)
    eng.close_session(s.session_id, opened + timedelta(days=days))
    intent = eng.raise_intent(s.session_id, MSISDN)
    momo = SimulatedCollections()
    momo.request_to_pay(intent)
    eng.settle(momo.poll(intent))
    return s


def test_successful_settlement_confirms_session() -> None:
    eng = BillingEngine()
    s = _settled_session(eng, T0, 3)
    assert s.settlement is SettlementState.CONFIRMED
    assert s.counts_toward_credit
    assert s.provider_reference


def test_failed_payment_leaves_session_uncredited() -> None:
    eng = BillingEngine()
    s = eng.open_session(pod_id=POD, farmer_id=FARMER, crates=4, opened_at=T0)
    eng.close_session(s.session_id, T0 + timedelta(days=2))
    intent = eng.raise_intent(s.session_id, MSISDN)
    momo = SimulatedCollections(failure_rate=1.0)
    momo.request_to_pay(intent)
    eng.settle(momo.poll(intent))
    assert s.settlement is SettlementState.FAILED
    assert not s.counts_toward_credit


def test_underpayment_is_not_partial_success() -> None:
    from hgledger import PaymentCallback

    eng = BillingEngine()
    s = eng.open_session(pod_id=POD, farmer_id=FARMER, crates=4, opened_at=T0)
    eng.close_session(s.session_id, T0 + timedelta(days=3))
    intent = eng.raise_intent(s.session_id, MSISDN)
    eng.settle(PaymentCallback(intent_id=intent.intent_id, provider_reference="short01",
                               succeeded=True, amount_ghs=Decimal("10.00")))
    assert s.settlement is SettlementState.FAILED


def test_bad_msisdn_is_rejected() -> None:
    eng = BillingEngine()
    s = eng.open_session(pod_id=POD, farmer_id=FARMER, crates=1, opened_at=T0)
    eng.close_session(s.session_id, T0 + timedelta(days=1))
    with pytest.raises(Exception):
        eng.raise_intent(s.session_id, "0241234567")  # missing 233 country prefix


# --- credit ----------------------------------------------------------------------

def test_unsettled_history_yields_no_credit(ledger: EventLedger) -> None:
    eng = BillingEngine()
    s = eng.open_session(pod_id=POD, farmer_id=FARMER, crates=4, opened_at=T0)
    eng.close_session(s.session_id, T0 + timedelta(days=2))
    profile = build_profile(FARMER, eng.sessions(), ledger.head, chain_valid=True)
    assert profile.band == "insufficient"
    assert profile.score == 0
    assert not profile.is_lendable


def test_sustained_settled_history_becomes_lendable(ledger: EventLedger) -> None:
    eng = BillingEngine()
    for month in range(8):
        for rep in range(3):
            opened = T0 + timedelta(days=30 * month + rep * 7)
            _settled_session(eng, opened, days=4, crates=6)
    profile = build_profile(FARMER, eng.sessions(), ledger.head, chain_valid=True)
    assert profile.sessions == 24
    assert profile.distinct_months_active >= 6
    assert profile.on_time_settlement_rate == pytest.approx(1.0)
    assert profile.is_lendable
    assert profile.band in ("emerging", "established")


def test_score_is_monotone_in_history() -> None:
    def score_for(months: int) -> int:
        eng = BillingEngine()
        for m in range(months):
            _settled_session(eng, T0 + timedelta(days=30 * m), days=3)
        return build_profile(FARMER, eng.sessions(), "x" * 64, chain_valid=True).score

    scores = [score_for(m) for m in (1, 3, 6, 12)]
    assert scores == sorted(scores), scores


def test_broken_chain_blocks_profile_issuance() -> None:
    eng = BillingEngine()
    _settled_session(eng, T0, 3)
    with pytest.raises(ValueError, match="unverified chain"):
        build_profile(FARMER, eng.sessions(), "x" * 64, chain_valid=False)


def test_profile_pins_the_evidence_head(ledger: EventLedger, key: str) -> None:
    """A lender must be able to say which chain state the profile was derived from."""
    ledger.append(signed(make_event(), key))
    eng = BillingEngine()
    _settled_session(eng, T0, 3)
    profile = build_profile(FARMER, eng.sessions(), ledger.head, chain_valid=True)
    assert profile.evidence_chain_head == ledger.head


def test_other_farmers_history_is_not_counted(ledger: EventLedger) -> None:
    eng = BillingEngine()
    _settled_session(eng, T0, 3)
    other = eng.open_session(pod_id=POD, farmer_id="FRM-9999", crates=40, opened_at=T0)
    eng.close_session(other.session_id, T0 + timedelta(days=30))
    profile = build_profile(FARMER, eng.sessions(), ledger.head, chain_valid=True)
    assert profile.sessions == 1
