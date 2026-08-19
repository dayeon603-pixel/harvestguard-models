"""HarvestGuard trust rail: signed pod events, a tamper-evident ledger, mobile-money
settlement, and the farmer credit profile derived from them.

The pod earns the daily revenue. This package is the part that compounds.
"""

from .billing import PRICE_PER_CRATE_DAY_GHS, BillingEngine, amount_for, crate_days
from .credit import build_profile
from .crypto import GENESIS_HASH, new_device_key, sign, verify
from .ledger import BadSignature, EventLedger, LedgerError, ReplayedEvent, UnknownPod
from .models import (
    CrateClass,
    EventType,
    FarmerCreditProfile,
    LedgerRecord,
    PaymentCallback,
    PaymentIntent,
    PodEvent,
    SettlementState,
    SignedPodEvent,
    StorageSession,
)
from .momo import MERCHANT_FEE_RATE, Collections, SimulatedCollections

__version__ = "0.1.0"

__all__ = [
    "PRICE_PER_CRATE_DAY_GHS", "BillingEngine", "amount_for", "crate_days",
    "build_profile",
    "GENESIS_HASH", "new_device_key", "sign", "verify",
    "BadSignature", "EventLedger", "LedgerError", "ReplayedEvent", "UnknownPod",
    "CrateClass", "EventType", "FarmerCreditProfile", "LedgerRecord", "PaymentCallback",
    "PaymentIntent", "PodEvent", "SettlementState", "SignedPodEvent", "StorageSession",
    "MERCHANT_FEE_RATE", "Collections", "SimulatedCollections",
    "__version__",
]
