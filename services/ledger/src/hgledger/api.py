"""HTTP surface for the trust rail.

Deliberately small. The pod talks to four endpoints and a lender reads one. Everything the
service refuses to do is as important as what it does: it will not open a door without a
confirmed payment, and it will not issue a credit profile against a chain that does not verify.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from .billing import BillingEngine
from .credit import build_profile
from .ledger import BadSignature, EventLedger, LedgerError, ReplayedEvent, UnknownPod
from .models import FarmerCreditProfile, PaymentCallback, SignedPodEvent
from .momo import Collections, SimulatedCollections

logger = logging.getLogger(__name__)

app = FastAPI(
    title="HarvestGuard Trust Rail",
    version="0.1.0",
    summary="Signed pod events, a tamper-evident ledger, and the farmer credit record built on them.",
)

_ledger = EventLedger()
_billing = BillingEngine()
_momo: Collections = SimulatedCollections()


def get_ledger() -> EventLedger:
    return _ledger


def get_billing() -> BillingEngine:
    return _billing


class HealthResponse(BaseModel):
    status: str
    records: int
    chain_head: str
    chain_valid: bool


class OpenSessionRequest(BaseModel):
    pod_id: str
    farmer_id: str
    crates: int = Field(ge=1, le=40)
    opened_at: datetime


class CloseSessionRequest(BaseModel):
    closed_at: datetime
    msisdn: str = Field(pattern=r"^233[0-9]{9}$")


class UnlockDecision(BaseModel):
    """What the pod is told. `granted` drives the solenoid."""

    granted: bool
    reason: str
    session_id: UUID | None = None


@app.get("/health", response_model=HealthResponse)
def health(ledger: EventLedger = Depends(get_ledger)) -> HealthResponse:
    return HealthResponse(status="ok", records=len(ledger), chain_head=ledger.head,
                          chain_valid=ledger.verify_chain())


@app.get("/info")
def info(ledger: EventLedger = Depends(get_ledger)) -> dict[str, object]:
    from . import __version__
    from .billing import PRICE_PER_CRATE_DAY_GHS

    return {
        "service": "harvestguard-trust-rail",
        "version": __version__,
        "price_ghs_per_crate_day": str(PRICE_PER_CRATE_DAY_GHS),
        "payment_rail": "MTN MoMo Collections",
        "records": len(ledger),
        "chain_valid": ledger.verify_chain(),
    }


@app.post("/pods/{pod_id}/register", status_code=status.HTTP_201_CREATED)
def register_pod(pod_id: str, device_key: str,
                 ledger: EventLedger = Depends(get_ledger)) -> dict[str, str]:
    try:
        ledger.register_pod(pod_id, device_key)
    except LedgerError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"pod_id": pod_id, "status": "registered"}


@app.post("/events", status_code=status.HTTP_201_CREATED)
def submit_event(signed: SignedPodEvent,
                 ledger: EventLedger = Depends(get_ledger)) -> dict[str, object]:
    """Every pod assertion enters here or not at all."""
    try:
        record = ledger.append(signed)
    except UnknownPod as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except BadSignature as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except ReplayedEvent as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LedgerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"sequence": record.sequence, "record_hash": record.record_hash}


@app.post("/sessions", status_code=status.HTTP_201_CREATED)
def open_session(req: OpenSessionRequest,
                 billing: BillingEngine = Depends(get_billing)) -> dict[str, object]:
    s = billing.open_session(pod_id=req.pod_id, farmer_id=req.farmer_id,
                            crates=req.crates, opened_at=req.opened_at)
    return {"session_id": str(s.session_id), "settlement": s.settlement.value}


@app.post("/sessions/{session_id}/close")
def close_session(session_id: UUID, req: CloseSessionRequest,
                  billing: BillingEngine = Depends(get_billing)) -> dict[str, object]:
    try:
        s = billing.close_session(session_id, req.closed_at)
        intent = billing.raise_intent(session_id, req.msisdn)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    reference = _momo.request_to_pay(intent)
    return {"session_id": str(session_id), "amount_ghs": str(s.amount_ghs),
            "intent_id": str(intent.intent_id), "provider_reference": reference}


@app.post("/payments/callback")
def payment_callback(cb: PaymentCallback,
                     billing: BillingEngine = Depends(get_billing)) -> dict[str, str]:
    try:
        s = billing.settle(cb)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"session_id": str(s.session_id), "settlement": s.settlement.value}


@app.get("/pods/{pod_id}/unlock", response_model=UnlockDecision)
def unlock(pod_id: str, session_id: UUID,
           billing: BillingEngine = Depends(get_billing)) -> UnlockDecision:
    """The door opens on confirmed money, never on a promise."""
    try:
        s = billing.get(session_id)
    except KeyError:
        return UnlockDecision(granted=False, reason="unknown session")
    if s.pod_id != pod_id:
        return UnlockDecision(granted=False, reason="session belongs to another pod")
    if s.is_open:
        return UnlockDecision(granted=True, reason="deposit in progress", session_id=session_id)
    if s.settlement.value != "confirmed":
        return UnlockDecision(granted=False, reason=f"payment {s.settlement.value}",
                              session_id=session_id)
    return UnlockDecision(granted=True, reason="payment confirmed", session_id=session_id)


@app.get("/farmers/{farmer_id}/credit", response_model=FarmerCreditProfile)
def credit(farmer_id: str, ledger: EventLedger = Depends(get_ledger),
           billing: BillingEngine = Depends(get_billing)) -> FarmerCreditProfile:
    try:
        return build_profile(farmer_id, billing.sessions(), ledger.head,
                             chain_valid=ledger.verify_chain())
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
