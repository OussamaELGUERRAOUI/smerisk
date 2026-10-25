"""Reusable datasets (§12.5): encrypted in memory, isolated by caller, deleted after 24 hours or on request.

A large dataset is sent in numbered pages. The number of pages is given when the dataset is created, or at the end
with ``complete``. A page can be sent again (it replaces the previous one) and pages can arrive in any order. A use
case only reads a dataset once every page has arrived; the pages are then put back together in their order.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cryptography.fernet import Fernet
from fastapi import HTTPException

from smerisk import ComputationImpossible, InputTooLarge
from smerisk.contracts import MAX_RECORDS
from smerisk_service import logs

MAX_PAGES = 10_000

CONTRACTS = (
    "payslips",
    "absence_months",
    "employment_periods",
    "contracts",
    "payroll_baseline",
    "ledger_entries",
    "invoices",
    "bank_balances",
    "scheduled_flows",
    "timesheets",
    "missions",
    "consultants",
    "deals",
    "fixed_price_projects",
    "sales",
    "products",
    "forecast_log",
)
"""Raw data contracts of §10 that a dataset can hold."""


class DatasetState(StrEnum):
    OPEN = "OPEN"
    """Pages are missing, or the number of pages is not known yet."""
    COMPLETE = "COMPLETE"
    """Every page has arrived: use cases can read the dataset."""


@dataclass
class _Page:
    token: bytes
    counts: Counter[str]
    size: int


@dataclass
class _Entry:
    caller: str
    expires: float
    total_pages: int | None = None
    pages: dict[int, _Page] = field(default_factory=dict)

    def missing(self) -> list[int]:
        return [n for n in range(1, (self.total_pages or 0) + 1) if n not in self.pages]

    def state(self) -> DatasetState:
        complete = self.total_pages is not None and not self.missing()
        return DatasetState.COMPLETE if complete else DatasetState.OPEN


@dataclass(frozen=True)
class DatasetSummary:
    dataset_id: str
    state: DatasetState
    total_pages: int | None
    received_pages: list[int]
    missing_pages: list[int]
    counts: dict[str, int]
    """Records by contract, all pages together."""
    size: int
    expires: float


@dataclass
class DatasetStore:
    ttl_seconds: int
    max_per_caller: int
    max_bytes: int
    key: bytes
    _entries: dict[str, _Entry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._fernet = Fernet(self.key)

    def create(self, caller: str, total_pages: int | None, first_page: dict[str, list[Any]] | None) -> DatasetSummary:
        """A new dataset. With data and no number of pages, it is a single page and complete at once."""
        self.purge()
        if first_page and total_pages is None:
            total_pages = 1
        with self._lock:
            if sum(e.caller == caller for e in self._entries.values()) >= self.max_per_caller:
                raise HTTPException(429, f"at most {self.max_per_caller} datasets per caller; delete one first")
            dataset_id = uuid.uuid4().hex
            entry = _Entry(caller=caller, expires=time.time() + self.ttl_seconds, total_pages=total_pages)
            if first_page:
                self._put_page(entry, 1, first_page)
            self._entries[dataset_id] = entry
            summary = self._summary(dataset_id, entry)
        logs.event(
            "dataset.created",
            dataset_id=dataset_id,
            caller=caller,
            total_pages=total_pages,
            state=summary.state.value,
            records=summary.counts,
        )
        return summary

    def put_page(self, caller: str, dataset_id: str, number: int, data: dict[str, list[Any]]) -> DatasetSummary:
        """Store page ``number``, replacing it if it was already sent."""
        self.purge()
        with self._lock:
            entry = self._owned(caller, dataset_id)
            limit = entry.total_pages or MAX_PAGES
            if not 1 <= number <= limit:
                raise HTTPException(422, f"page {number} is outside 1 to {limit}")
            replaced = number in entry.pages
            self._put_page(entry, number, data)
            summary = self._summary(dataset_id, entry)
        logs.event(
            "dataset.page",
            dataset_id=dataset_id,
            caller=caller,
            page=number,
            replaced=replaced,
            page_records={k: len(v) for k, v in data.items()},
            received=len(summary.received_pages),
            total_pages=summary.total_pages,
            state=summary.state.value,
        )
        return summary

    def complete(self, caller: str, dataset_id: str, total_pages: int) -> DatasetSummary:
        """Give the number of pages at the end; 409 if pages are missing or if it contradicts the one given before."""
        with self._lock:
            entry = self._owned(caller, dataset_id)
            if entry.total_pages is not None and entry.total_pages != total_pages:
                raise ComputationImpossible(
                    "TOTAL_PAGES_MISMATCH",
                    f"the dataset was created with {entry.total_pages} pages, not {total_pages}",
                    {"total_pages": entry.total_pages},
                )
            if max(entry.pages, default=0) > total_pages:
                raise HTTPException(422, f"page {max(entry.pages)} was received: total_pages cannot be {total_pages}")
            entry.total_pages = total_pages
            summary = self._summary(dataset_id, entry)
        if summary.missing_pages:
            raise self._incomplete(summary)
        logs.event("dataset.completed", dataset_id=dataset_id, caller=caller, pages=total_pages, records=summary.counts)
        return summary

    def status(self, caller: str, dataset_id: str) -> DatasetSummary:
        self.purge()
        with self._lock:
            return self._summary(dataset_id, self._owned(caller, dataset_id))

    def get(self, caller: str, dataset_id: str) -> dict[str, list[Any]]:
        """The records of every page in page order; 409 ``DATASET_INCOMPLETE`` while a page is missing."""
        self.purge()
        with self._lock:
            entry = self._owned(caller, dataset_id)
            summary = self._summary(dataset_id, entry)
            pages = [entry.pages[n].token for n in sorted(entry.pages)]
        if summary.state is not DatasetState.COMPLETE:
            raise self._incomplete(summary)
        data: dict[str, list[Any]] = {}
        for token in pages:
            for name, rows in json.loads(self._fernet.decrypt(token)).items():
                data.setdefault(name, []).extend(rows)
        logs.event("dataset.read", logging.DEBUG, dataset_id=dataset_id, pages=len(pages))
        return data

    def delete(self, caller: str, dataset_id: str) -> None:
        with self._lock:
            self._owned(caller, dataset_id)
            del self._entries[dataset_id]
        logs.event("dataset.deleted", dataset_id=dataset_id, caller=caller)

    def _put_page(self, entry: _Entry, number: int, data: dict[str, list[Any]]) -> None:
        """Check the limits with this page in place of the previous one, then store it encrypted."""
        others = [p for n, p in entry.pages.items() if n != number]
        counts = sum((p.counts for p in others), Counter[str]())
        for name, rows in data.items():
            if counts[name] + len(rows) > MAX_RECORDS:
                raise InputTooLarge(name, counts[name] + len(rows), MAX_RECORDS)
        plain = json.dumps(data, separators=(",", ":")).encode()
        size = sum(p.size for p in others) + len(plain)
        if size > self.max_bytes:
            raise InputTooLarge("dataset", size, self.max_bytes)
        entry.pages[number] = _Page(
            self._fernet.encrypt(plain), Counter({k: len(v) for k, v in data.items()}), len(plain)
        )
        entry.expires = time.time() + self.ttl_seconds

    def _owned(self, caller: str, dataset_id: str) -> _Entry:
        """404 for an unknown dataset and for another caller's, without telling which."""
        entry = self._entries.get(dataset_id)
        if entry is None or entry.caller != caller:
            raise HTTPException(404, "unknown or expired dataset")
        return entry

    @staticmethod
    def _summary(dataset_id: str, entry: _Entry) -> DatasetSummary:
        counts = sum((p.counts for p in entry.pages.values()), Counter[str]())
        return DatasetSummary(
            dataset_id=dataset_id,
            state=entry.state(),
            total_pages=entry.total_pages,
            received_pages=sorted(entry.pages),
            missing_pages=entry.missing(),
            counts=dict(counts),
            size=sum(p.size for p in entry.pages.values()),
            expires=entry.expires,
        )

    @staticmethod
    def _incomplete(summary: DatasetSummary) -> ComputationImpossible:
        if summary.total_pages is None:
            message = "the number of pages is not known: call POST /v1/datasets/{id}/complete after the last page"
        else:
            message = f"{len(summary.missing_pages)} of {summary.total_pages} pages missing"
        return ComputationImpossible(
            "DATASET_INCOMPLETE",
            message,
            {"total_pages": summary.total_pages, "missing_pages": summary.missing_pages[:100]},
        )

    def purge(self) -> None:
        now = time.time()
        with self._lock:
            expired = [i for i, e in self._entries.items() if e.expires < now]
            for dataset_id in expired:
                del self._entries[dataset_id]
        if expired:
            logs.event("dataset.expired", count=len(expired))
