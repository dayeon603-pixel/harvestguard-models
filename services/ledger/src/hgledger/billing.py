"""Per-crate-day billing and the session state machine.

Pricing is GHS 2.16 per crate per day, derived in `sim/GHANA_UNIT_ECONOMICS.md` from what a
10 kg crate of tomatoes is worth to a Ghanaian smallholder on avoided spoilage alone, not
from a competitor's rate card. Part-days round up: the farmer is told this at deposit, and
rounding down would let a pod be used as free overnight storage.

The state machine exists to enforce one rule that the whole credit product depends on:
**a session becomes a credit fact only when the money is confirmed.** Everything else is
provisional.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Final
from uuid import UUID

from .models import (
    PaymentCallback,
    PaymentIntent,
    SettlementState,
    StorageSession,
)

__all__ = ["PRICE_PER_CRATE_DAY_GHS", "crate_days", "amount_for", "BillingEngine"]

logger = logging.getLogger(__name__)

PRICE_PER_CRATE_DAY_GHS: Final[Decimal] = Decimal("2.16")
_SECONDS_PER_DAY: Final[int] = 86_400


def crate_days(crates: int, opened_at: datetime, closed_at: datetime) -> int:
    """Chargeable crate-days. Any started day is a full day."""
    if closed_at < opened_at:
        raise ValueError("closed_at precedes opened_at")
    seconds = (closed_at - opened_at).total_seconds()
    days = max(1, -(-int(seconds) // _SECONDS_PER_DAY))  # ceil, minimum one day
    return crates * days


def amount_for(crates: int, opened_at: datetime, closed_at: datetime) -> Decimal:
    total = PRICE_PER_CRATE_DAY_GHS * crate_days(crates, opened_at, closed_at)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class BillingEngine:
    """Owns session lifecycle and settlement."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, StorageSession] = {}
        self._intents: dict[UUID, PaymentIntent] = {}

    def open_session(self, *, pod_id: str, farmer_id: str, crates: int,
                     opened_at: datetime, **kw) -> StorageSession:
        session = StorageSession(pod_id=pod_id, farmer_id=farmer_id, crates=crates,
                                 opened_at=opened_at, **kw)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: UUID) -> StorageSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown session {session_id}") from exc

    def sessions(self) -> tuple[StorageSession, ...]:
        return tuple(self._sessions.values())

    def close_session(self, session_id: UUID, closed_at: datetime) -> StorageSession:
        session = self.get(session_id)
        if not session.is_open:
            raise ValueError(f"session {session_id} already closed")
        session.closed_at = closed_at
        session.amount_ghs = amount_for(session.crates, session.opened_at, closed_at)
        return session

    def raise_intent(self, session_id: UUID, msisdn: str) -> PaymentIntent:
        session = self.get(session_id)
        if session.is_open:
            raise ValueError("cannot bill an open session")
        intent = PaymentIntent(session_id=session_id, farmer_id=session.farmer_id,
                               msisdn=msisdn, amount_ghs=session.amount_ghs)
        self._intents[intent.intent_id] = intent
        return intent

    def intent(self, intent_id: UUID) -> PaymentIntent:
        return self._intents[intent_id]

    def settle(self, callback: PaymentCallback) -> StorageSession:
        """Apply a provider callback. Underpayment is a failure, not a partial success."""
        intent = self._intents.get(callback.intent_id)
        if intent is None:
            raise KeyError(f"unknown intent {callback.intent_id}")
        session = self.get(intent.session_id)
        if callback.succeeded and callback.amount_ghs >= session.amount_ghs:
            session.settlement = SettlementState.CONFIRMED
            session.provider_reference = callback.provider_reference
        else:
            session.settlement = SettlementState.FAILED
            logger.warning("settlement failed for session %s", session.session_id)
        return session
