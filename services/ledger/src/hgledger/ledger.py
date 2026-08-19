"""Append-only, hash-chained ledger of verified pod events.

Every event is checked in a fixed order and rejected at the first failure:

1. the pod is registered,
2. the device signature verifies over the event's canonical form,
3. the event has not been seen before (replay),
4. the event is not implausibly out of order for that pod.

Only then is it sealed into the chain. `verify_chain` re-derives every hash from genesis, so
any post-hoc edit or deletion is detectable by anyone holding the records, including a lender
who does not trust the operator.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Final
from uuid import UUID

from .crypto import GENESIS_HASH, chain_hash, verify
from .models import LedgerRecord, PodEvent, SignedPodEvent

__all__ = ["LedgerError", "UnknownPod", "BadSignature", "ReplayedEvent", "EventLedger"]

logger = logging.getLogger(__name__)

MAX_CLOCK_SKEW: Final[timedelta] = timedelta(hours=26)


class LedgerError(Exception):
    """Base class for anything that stops an event entering the chain."""


class UnknownPod(LedgerError):
    """Event from a pod that was never provisioned."""


class BadSignature(LedgerError):
    """Signature does not verify under the pod's device key."""


class ReplayedEvent(LedgerError):
    """This event id is already in the chain."""


class OutOfOrder(LedgerError):
    """Event timestamp is implausible relative to the pod's history."""


class EventLedger:
    """In-memory reference implementation of the append-only chain.

    The production store is PostgreSQL with an insert-only table and no UPDATE or DELETE
    grant; the invariants enforced here are the ones that schema has to preserve.
    """

    def __init__(self) -> None:
        self._records: list[LedgerRecord] = []
        self._device_keys: dict[str, str] = {}
        self._seen: set[UUID] = set()
        self._last_seen_at: dict[str, object] = {}

    # -- provisioning -------------------------------------------------------------
    def register_pod(self, pod_id: str, device_key_hex: str) -> None:
        if pod_id in self._device_keys:
            raise LedgerError(f"pod {pod_id} already registered")
        self._device_keys[pod_id] = device_key_hex

    @property
    def head(self) -> str:
        return self._records[-1].record_hash if self._records else GENESIS_HASH

    def __len__(self) -> int:
        return len(self._records)

    # -- append -------------------------------------------------------------------
    def append(self, signed: SignedPodEvent) -> LedgerRecord:
        event = signed.event
        key = self._device_keys.get(event.pod_id)
        if key is None:
            raise UnknownPod(f"pod {event.pod_id} is not registered")

        canonical = event.canonical()
        if not verify(key, canonical, signed.signature):
            raise BadSignature(f"signature does not verify for event {event.event_id}")

        if event.event_id in self._seen:
            raise ReplayedEvent(f"event {event.event_id} already recorded")

        last = self._last_seen_at.get(event.pod_id)
        if last is not None and event.occurred_at < last - MAX_CLOCK_SKEW:  # type: ignore[operator]
            raise OutOfOrder(
                f"event {event.event_id} predates pod {event.pod_id} history beyond skew"
            )

        prev = self.head
        record = LedgerRecord(
            sequence=len(self._records),
            event=event,
            signature=signed.signature,
            prev_hash=prev,
            record_hash=chain_hash(prev, canonical, signed.signature),
        )
        self._records.append(record)
        self._seen.add(event.event_id)
        self._last_seen_at[event.pod_id] = event.occurred_at
        return record

    # -- read -------------------------------------------------------------------
    def records(self) -> tuple[LedgerRecord, ...]:
        return tuple(self._records)

    def for_farmer(self, farmer_id: str) -> Iterator[LedgerRecord]:
        return (r for r in self._records if r.event.farmer_id == farmer_id)

    def verify_chain(self) -> bool:
        """Re-derive every hash from genesis. False if anything was altered."""
        prev = GENESIS_HASH
        for i, rec in enumerate(self._records):
            if rec.sequence != i or rec.prev_hash != prev:
                logger.error("chain break at sequence %d", i)
                return False
            key = self._device_keys.get(rec.event.pod_id)
            canonical = rec.event.canonical()
            if key is None or not verify(key, canonical, rec.signature):
                logger.error("signature failure at sequence %d", i)
                return False
            if chain_hash(prev, canonical, rec.signature) != rec.record_hash:
                logger.error("hash mismatch at sequence %d", i)
                return False
            prev = rec.record_hash
        return True
