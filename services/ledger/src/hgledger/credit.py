"""Credit profile derived only from chain-verified, money-settled storage history.

This is the asset the pitch claims exists. It is worth being precise about what it is and is
not, because a lender will be.

**What it is.** A record of behaviour the operator observed directly and was paid for: how
many crate-days a farmer stored, over how many distinct months, how reliably they settled.
Every input is a session that (a) sits in a hash chain that verifies from genesis, (b) carries
a device signature from a provisioned pod, and (c) has a confirmed mobile-money reference.

**What it is not.** It is not a credit score in the regulated sense, it does not predict
default, and it has never been validated against repayment outcomes because no loans have
been written against it. It is a thin-file evidence pack that gives a lender something to
underwrite where today there is nothing at all. Calling it more than that would be the kind
of claim that ends a bank conversation.

Scoring is deliberately transparent and monotone rather than learned: with zero default
outcomes to train on, a fitted model would encode nothing but our own assumptions while
looking authoritative.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Final

from .models import FarmerCreditProfile, SettlementState, StorageSession

__all__ = ["build_profile", "SCORE_WEIGHTS"]

logger = logging.getLogger(__name__)

# Component ceilings, summing to 1000. Depth of history is weighted highest because
# persistence across seasons is the only thing here that a lender cannot observe elsewhere.
SCORE_WEIGHTS: Final[dict[str, int]] = {
    "months_active": 400,
    "crate_days": 300,
    "settlement_reliability": 200,
    "recency": 100,
}

_MONTHS_SATURATION: Final[int] = 12
_CRATE_DAYS_SATURATION: Final[int] = 600
_THIN_MIN_SESSIONS: Final[int] = 3
_EMERGING_SCORE: Final[int] = 350
_ESTABLISHED_SCORE: Final[int] = 650


def _band(score: int, sessions: int, months: int) -> str:
    if sessions < _THIN_MIN_SESSIONS:
        return "insufficient"
    if score >= _ESTABLISHED_SCORE and months >= 6:
        return "established"
    if score >= _EMERGING_SCORE:
        return "emerging"
    return "thin"


def build_profile(farmer_id: str, sessions: Iterable[StorageSession],
                  chain_head: str, *, chain_valid: bool) -> FarmerCreditProfile:
    """Assemble a lender-facing profile.

    If the chain does not verify, no profile is issued at any score. A tamper-evident record
    that is not actually verified is worse than no record, because it invites reliance.
    """
    if not chain_valid:
        raise ValueError("refusing to issue a credit profile against an unverified chain")

    all_sessions = [s for s in sessions if s.farmer_id == farmer_id]
    settled = [s for s in all_sessions if s.counts_toward_credit]
    closed = [s for s in all_sessions if s.closed_at is not None]

    if not settled:
        return FarmerCreditProfile(
            farmer_id=farmer_id, sessions=0, crate_days=0,
            total_paid_ghs=Decimal("0.00"), distinct_months_active=0,
            on_time_settlement_rate=0.0, score=0, band="insufficient",
            evidence_chain_head=chain_head,
        )

    from .billing import PRICE_PER_CRATE_DAY_GHS, crate_days as _crate_days

    total_crate_days = sum(
        _crate_days(s.crates, s.opened_at, s.closed_at) for s in settled  # type: ignore[arg-type]
    )
    total_paid = sum((s.amount_ghs for s in settled), Decimal("0.00"))
    months = {(s.opened_at.year, s.opened_at.month) for s in settled}
    reliability = len(settled) / len(closed) if closed else 0.0

    first = min(s.opened_at for s in settled)
    last = max(s.closed_at for s in settled)  # type: ignore[type-var]

    months_pts = SCORE_WEIGHTS["months_active"] * min(len(months) / _MONTHS_SATURATION, 1.0)
    volume_pts = SCORE_WEIGHTS["crate_days"] * min(total_crate_days / _CRATE_DAYS_SATURATION, 1.0)
    reliability_pts = SCORE_WEIGHTS["settlement_reliability"] * reliability
    span_days = max((last - first).days, 1)
    recency_pts = SCORE_WEIGHTS["recency"] * min(len(settled) / (span_days / 30.0 + 1.0) / 4.0, 1.0)

    score = int(round(months_pts + volume_pts + reliability_pts + recency_pts))
    score = max(0, min(score, 1000))

    return FarmerCreditProfile(
        farmer_id=farmer_id,
        sessions=len(settled),
        crate_days=total_crate_days,
        total_paid_ghs=total_paid.quantize(Decimal("0.01")),
        distinct_months_active=len(months),
        on_time_settlement_rate=round(reliability, 4),
        first_seen=first,
        last_seen=last,
        score=score,
        band=_band(score, len(settled), len(months)),
        evidence_chain_head=chain_head,
    )
