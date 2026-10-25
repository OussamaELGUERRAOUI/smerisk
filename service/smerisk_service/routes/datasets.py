"""/v1/datasets: reusable datasets retained for 24 hours, sent at once or in numbered pages (§12.5)."""

from __future__ import annotations

import datetime as dt
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, create_model

from smerisk.contracts import MAX_RECORDS, IgnoredRecord, read_fec
from smerisk_service.auth import caller
from smerisk_service.datasets import CONTRACTS, MAX_PAGES, DatasetState, DatasetStore, DatasetSummary
from smerisk_service.errors import ERROR_RESPONSES

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])

_CONTRACT_FIELDS: dict[str, Any] = {
    name: (list[dict[str, Any]] | None, Field(None, max_length=MAX_RECORDS)) for name in CONTRACTS
}
DatasetPage: type[BaseModel] = create_model("DatasetPage", __config__=ConfigDict(extra="forbid"), **_CONTRACT_FIELDS)
"""Raw data of §10, by contract name; records are validated when a use case reads them."""
DatasetCreate: type[BaseModel] = create_model(
    "DatasetCreate",
    __config__=ConfigDict(extra="forbid"),
    total_pages=(
        int | None,
        Field(
            None,
            ge=1,
            le=MAX_PAGES,
            description="Number of pages when the data is sent in pages; the records of this body are then page 1. "
            "Without it, the records of this body are the whole dataset.",
        ),
    ),
    **_CONTRACT_FIELDS,
)

PageNumber = Annotated[int, Path(ge=1, le=MAX_PAGES)]
Caller = Annotated[str, Depends(caller)]
FecFile = Annotated[
    UploadFile, File(description="FEC file as exported (tab, | or ; separated), or a part of it with its header line")
]
OtherContracts = Annotated[str | None, Form(description="Other contracts, as a JSON page")]
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Unknown or expired dataset"}}


class FecLines(BaseModel):
    count: int
    """Lines set aside (wrong number of fields, invalid date or amount)."""
    listed: list[IgnoredRecord]
    """The first 100, with their position among the data lines of the file."""


class DatasetStatus(BaseModel):
    dataset_id: str
    status: DatasetState
    """``COMPLETE`` once every page has arrived: only then can a use case read the dataset."""
    total_pages: int | None
    """``None`` until given, at creation or with ``complete``."""
    received_pages: list[int]
    missing_pages: list[int]
    contracts: dict[str, int]
    """Records stored by contract, all pages together."""
    bytes: int
    expires_at: dt.datetime
    """24 hours after the last page."""
    ignored_fec_lines: FecLines | None = None


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_pages: int = Field(ge=1, le=MAX_PAGES)


def _status(summary: DatasetSummary, ignored: FecLines | None = None) -> DatasetStatus:
    return DatasetStatus(
        dataset_id=summary.dataset_id,
        status=summary.state,
        total_pages=summary.total_pages,
        received_pages=summary.received_pages,
        missing_pages=summary.missing_pages,
        contracts=summary.counts,
        bytes=summary.size,
        expires_at=dt.datetime.fromtimestamp(summary.expires, dt.UTC),
        ignored_fec_lines=ignored,
    )


def _records(body: BaseModel) -> dict[str, list[Any]]:
    return {name: rows for name, rows in body.model_dump(exclude_none=True).items() if name in CONTRACTS and rows}


def _store(request: Request) -> DatasetStore:
    store: DatasetStore = request.app.state.datasets
    return store


async def _fec_page(fec: UploadFile, data: str | None) -> tuple[dict[str, list[Any]], FecLines | None]:
    parsed = read_fec(await fec.read())
    others: dict[str, Any] = {}
    if data:
        try:
            others = _records(DatasetPage.model_validate(json.loads(data)))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, f"data: {type(exc).__name__}") from None
    others.pop("ledger_entries", None)
    lines = [e.model_dump(mode="json", by_alias=True) for e in parsed.records]
    ignored = FecLines(count=parsed.n_ignored, listed=parsed.ignored) if parsed.n_ignored else None
    page = {name: rows for name, rows in {**others, "ledger_entries": lines}.items() if rows}
    if not page:
        raise HTTPException(422, "the FEC file has no readable line")
    return page, ignored


@router.post("", status_code=201, responses=ERROR_RESPONSES)
def create_dataset(body: DatasetCreate, request: Request, who: Caller) -> DatasetStatus:
    """Create a dataset.

    - With records and no ``total_pages``: the records are the whole dataset, complete at once.
    - With ``total_pages``: the dataset expects that many pages; records in this body are page 1.
    - With neither: pages of unknown number; call ``complete`` after the last one.
    """
    total = getattr(body, "total_pages", None)
    records = _records(body)
    return _status(_store(request).create(who, total, records or None))


@router.put("/{dataset_id}/pages/{number}", responses={**ERROR_RESPONSES, **NOT_FOUND})
def put_page(dataset_id: str, number: PageNumber, body: DatasetPage, request: Request, who: Caller) -> DatasetStatus:
    """Send page ``number`` (from 1). Sending a page again replaces it: a retry never duplicates records."""
    records = _records(body)
    if not records:
        raise HTTPException(422, "a page needs at least one contract with records")
    return _status(_store(request).put_page(who, dataset_id, number, records))


@router.put("/{dataset_id}/pages/{number}/fec", responses={**ERROR_RESPONSES, **NOT_FOUND})
async def put_fec_page(
    dataset_id: str, number: PageNumber, request: Request, who: Caller, fec: FecFile, data: OtherContracts = None
) -> DatasetStatus:
    """Send page ``number`` as a FEC file, or as a part of one with its header line."""
    page, ignored = await _fec_page(fec, data)
    return _status(_store(request).put_page(who, dataset_id, number, page), ignored)


@router.post("/{dataset_id}/complete", responses={**ERROR_RESPONSES, **NOT_FOUND})
def complete_dataset(dataset_id: str, body: Completion, request: Request, who: Caller) -> DatasetStatus:
    """Give the number of pages after the last one; 409 ``DATASET_INCOMPLETE`` with the missing pages."""
    return _status(_store(request).complete(who, dataset_id, body.total_pages))


@router.get("/{dataset_id}", responses={401: ERROR_RESPONSES[401], **NOT_FOUND})
def get_dataset(dataset_id: str, request: Request, who: Caller) -> DatasetStatus:
    """State of the dataset: pages received and missing, records by contract. The records are never returned."""
    return _status(_store(request).status(who, dataset_id))


@router.post("/fec", status_code=201, responses=ERROR_RESPONSES)
async def create_dataset_from_fec(
    request: Request, who: Caller, fec: FecFile, data: OtherContracts = None
) -> DatasetStatus:
    """A complete dataset from one FEC file (labels dropped), with other contracts if given."""
    page, ignored = await _fec_page(fec, data)
    return _status(_store(request).create(who, None, page), ignored)


@router.delete("/{dataset_id}", status_code=204, responses={401: ERROR_RESPONSES[401], **NOT_FOUND})
def delete_dataset(dataset_id: str, request: Request, who: Caller) -> Response:
    _store(request).delete(who, dataset_id)
    return Response(status_code=204)
