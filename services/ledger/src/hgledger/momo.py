"""MTN MoMo collections adapter.

The production path is MTN MoMo Collections: request-to-pay, then a callback confirming
settlement. Merchant acquiring is borne by the merchant (0.75-1.5%), and Ghana's E-Levy was
repealed on 2 April 2025, so the farmer pays the quoted crate price with nothing deducted.

`SimulatedCollections` implements the same protocol deterministically so the whole rail can
be exercised end to end without network access or a merchant account, which is what makes
this testable before onboarding. It is a test double, not a mock of a live integration: the
state machine it drives is the real one.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from .models import PaymentCallback, PaymentIntent, utcnow

__all__ = ["Collections", "SimulatedCollections", "MERCHANT_FEE_RATE"]

logger = logging.getLogger(__name__)

MERCHANT_FEE_RATE: Decimal = Decimal("0.010")   # mid of MTN's 0.75-1.5% band
_E_LEVY_RATE: Decimal = Decimal("0.000")        # Ghana E-Levy repealed 2 April 2025


class Collections(Protocol):
    """The provider surface the rail depends on."""

    def request_to_pay(self, intent: PaymentIntent) -> str: ...

    def poll(self, intent: PaymentIntent) -> PaymentCallback | None: ...


class SimulatedCollections:
    """Deterministic stand-in for MTN MoMo Collections.

    Failure is derived from a hash of the MSISDN and intent so a given farmer fails
    reproducibly, which is what makes settlement-failure paths testable rather than flaky.
    """

    def __init__(self, failure_rate: float = 0.0) -> None:
        if not 0.0 <= failure_rate <= 1.0:
            raise ValueError("failure_rate must be within [0, 1]")
        self._failure_rate = failure_rate
        self._requested: dict[UUID, PaymentIntent] = {}

    def request_to_pay(self, intent: PaymentIntent) -> str:
        self._requested[intent.intent_id] = intent
        ref = hashlib.sha256(f"{intent.intent_id}{intent.msisdn}".encode()).hexdigest()[:16]
        logger.debug("request-to-pay %s GHS %s -> %s", intent.msisdn, intent.amount_ghs, ref)
        return ref

    def _fails(self, intent: PaymentIntent) -> bool:
        if self._failure_rate <= 0.0:
            return False
        digest = hashlib.sha256(f"{intent.intent_id}".encode()).digest()
        return (digest[0] / 255.0) < self._failure_rate

    def poll(self, intent: PaymentIntent) -> PaymentCallback | None:
        if intent.intent_id not in self._requested:
            return None
        ref = hashlib.sha256(f"{intent.intent_id}{intent.msisdn}".encode()).hexdigest()[:16]
        ok = not self._fails(intent)
        return PaymentCallback(
            intent_id=intent.intent_id,
            provider_reference=ref,
            succeeded=ok,
            amount_ghs=intent.amount_ghs if ok else Decimal("0.00"),
            settled_at=utcnow() + timedelta(seconds=4),
        )


def net_of_fees(gross_ghs: Decimal) -> Decimal:
    """What the operator actually banks after acquiring fees."""
    total_rate = MERCHANT_FEE_RATE + _E_LEVY_RATE
    return (gross_ghs * (Decimal("1.00") - total_rate)).quantize(Decimal("0.01"))
