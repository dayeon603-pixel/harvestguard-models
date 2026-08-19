"""Domain models for the HarvestGuard trust rail.

The product thesis is that the pod is the wedge and the verified record is the business. That
only holds if "verified" means something a bank would accept. Three properties have to be true
of every record that reaches a lender:

1. it was produced by a specific pod and not by anyone else  (device signature)
2. it has not been altered since                             (hash chain)
3. the money it claims moved actually moved                  (payment reconciliation)

These models encode those properties in the type system so a record that fails any of them
cannot be constructed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "EventType",
    "SettlementState",
    "CrateClass",
    "PodEvent",
    "SignedPodEvent",
    "LedgerRecord",
    "StorageSession",
    "PaymentIntent",
    "PaymentCallback",
    "FarmerCreditProfile",
]

Cedis = Annotated[Decimal, Field(max_digits=12, decimal_places=2, ge=0)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventType(StrEnum):
    """What a pod can assert. Anything not in this set is rejected at the edge."""

    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    TELEMETRY = "telemetry"
    DOOR_OPEN = "door_open"
    FAULT = "fault"


class SettlementState(StrEnum):
    """A deposit is not a credit fact until the money is confirmed."""

    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class CrateClass(StrEnum):
    """Grade asserted at deposit. Ungraded is the honest default."""

    UNGRADED = "ungraded"
    GRADE_A = "grade_a"
    GRADE_B = "grade_b"


class PodEvent(BaseModel):
    """An assertion made by a pod, before any signature is checked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    pod_id: str = Field(min_length=3, max_length=32)
    farmer_id: str = Field(min_length=3, max_length=32)
    event_type: EventType
    occurred_at: datetime
    crates: int = Field(default=0, ge=0, le=40)
    crate_class: CrateClass = CrateClass.UNGRADED
    box_temp_c: float | None = Field(default=None, ge=-30.0, le=60.0)
    battery_soc: float | None = Field(default=None, ge=0.0, le=1.0)
    payload: dict[str, str] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return v

    def canonical(self) -> str:
        """Deterministic serialisation. The signature covers exactly this string.

        Field order is fixed rather than taken from the model so that adding an optional
        field later cannot silently invalidate historical signatures.
        """
        return "|".join(
            [
                str(self.event_id),
                self.pod_id,
                self.farmer_id,
                self.event_type.value,
                self.occurred_at.astimezone(timezone.utc).isoformat(),
                str(self.crates),
                self.crate_class.value,
                "" if self.box_temp_c is None else f"{self.box_temp_c:.3f}",
                "" if self.battery_soc is None else f"{self.battery_soc:.4f}",
                ";".join(f"{k}={self.payload[k]}" for k in sorted(self.payload)),
            ]
        )


class SignedPodEvent(BaseModel):
    """A pod event carrying the device's HMAC over its canonical form."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: PodEvent
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class LedgerRecord(BaseModel):
    """A verified event, sealed into the hash chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=0)
    event: PodEvent
    signature: str
    prev_hash: str = Field(min_length=64, max_length=64)
    record_hash: str = Field(min_length=64, max_length=64)
    recorded_at: datetime = Field(default_factory=utcnow)


class PaymentIntent(BaseModel):
    """A request for money, raised before the door is allowed to open."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    farmer_id: str
    msisdn: str = Field(pattern=r"^233[0-9]{9}$", description="Ghana MSISDN, 233XXXXXXXXX")
    amount_ghs: Cedis
    created_at: datetime = Field(default_factory=utcnow)


class PaymentCallback(BaseModel):
    """What the mobile-money provider tells us happened."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: UUID
    provider_reference: str = Field(min_length=4, max_length=64)
    succeeded: bool
    amount_ghs: Cedis
    settled_at: datetime = Field(default_factory=utcnow)


class StorageSession(BaseModel):
    """One farmer's crates held in one pod across a billing window."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID = Field(default_factory=uuid4)
    pod_id: str
    farmer_id: str
    crates: int = Field(ge=1, le=40)
    crate_class: CrateClass = CrateClass.UNGRADED
    opened_at: datetime
    closed_at: datetime | None = None
    settlement: SettlementState = SettlementState.PROVISIONAL
    amount_ghs: Cedis = Decimal("0.00")
    provider_reference: str | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def counts_toward_credit(self) -> bool:
        """Only a closed, money-confirmed session is a credit fact."""
        return self.closed_at is not None and self.settlement is SettlementState.CONFIRMED


class FarmerCreditProfile(BaseModel):
    """Everything a lender is told, derived only from chain-verified settled sessions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    farmer_id: str
    sessions: int = Field(ge=0)
    crate_days: int = Field(ge=0)
    total_paid_ghs: Cedis
    distinct_months_active: int = Field(ge=0)
    on_time_settlement_rate: float = Field(ge=0.0, le=1.0)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    score: int = Field(ge=0, le=1000)
    band: Literal["insufficient", "thin", "emerging", "established"]
    evidence_chain_head: str

    @property
    def is_lendable(self) -> bool:
        return self.band in ("emerging", "established")
