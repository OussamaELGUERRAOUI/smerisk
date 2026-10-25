"""Read a raw FEC file (txt/csv) into LedgerEntry records (§10.2)."""

from __future__ import annotations

import csv
import io
import os

from smerisk.contracts._base import Parsed, parse_records
from smerisk.contracts.accounting import LedgerEntry
from smerisk.core.errors import FieldError, InputTooLarge, InvalidInput

MAX_FILE_BYTES = 200 * 1024 * 1024

REQUIRED_COLUMNS = ("JournalCode", "EcritureNum", "EcritureDate", "CompteNum")
_COLUMNS = {
    name.lower(): name
    for name in (
        *REQUIRED_COLUMNS,
        "CompAuxNum",
        "PieceRef",
        "PieceDate",
        "Debit",
        "Credit",
        "Montant",
        "Sens",
        "EcritureLet",
        "DateLet",
        "ValidDate",
    )
}
_SEPARATORS = ("\t", "|", ";")


def _decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), else Windows-1252: the FEC standard allows ISO 8859-15 and most exports use it."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_fec(source: str | os.PathLike[str] | bytes) -> Parsed[LedgerEntry]:
    """Read a FEC file: a path, or the content of the file.

    The separator (tab, ``|``, or ``;``) is detected from the header, and column names are matched regardless of
    case. Labels and unknown columns are dropped. A line that cannot be read is set aside in ``ignored``, with its
    index among the data lines (0 = the line after the header).
    """
    if isinstance(source, bytes):
        data = source
    else:
        size = os.path.getsize(source)
        if size > MAX_FILE_BYTES:
            raise InputTooLarge("ledger_entries", size, MAX_FILE_BYTES)
        with open(source, "rb") as file:
            data = file.read()
    if len(data) > MAX_FILE_BYTES:
        raise InputTooLarge("ledger_entries", len(data), MAX_FILE_BYTES)

    text = _decode(data)
    header_line = text.split("\n", 1)[0]
    separator = max(_SEPARATORS, key=header_line.count)
    if header_line.count(separator) == 0:
        raise InvalidInput([FieldError(("header",), "no column separator found (expected tab, | or ;)")])
    # Only spreadsheet exports (``;``) quote their fields; in a FEC, a quote is part of a label.
    stream = io.StringIO(text, newline="")
    if separator == ";":
        reader = csv.reader(stream, delimiter=separator)
    else:
        reader = csv.reader(stream, delimiter=separator, quoting=csv.QUOTE_NONE)

    header = [_COLUMNS.get(name.strip().lower()) for name in next(reader)]
    present = {name for name in header if name}
    missing = [name for name in REQUIRED_COLUMNS if name not in present]
    if not ({"Debit", "Credit"} <= present or {"Montant", "Sens"} <= present):
        missing.append("Debit and Credit (or Montant and Sens)")
    if missing:
        raise InvalidInput([FieldError(("header",), f"missing columns: {', '.join(missing)}")])

    rows: list[dict[str, str]] = []
    lengths: list[int] = []
    for line in reader:
        if not any(cell.strip() for cell in line):
            continue  # blank line, usually at the end of the file
        # A line with the wrong number of fields is shifted: it is sent empty so that it is always set aside.
        rows.append(
            {name: cell for name, cell in zip(header, line, strict=True) if name} if len(line) == len(header) else {}
        )
        lengths.append(len(line))

    parsed = parse_records(LedgerEntry, rows, name="ledger_entries")
    for ignored in parsed.ignored:
        if lengths[ignored.index] != len(header):
            ignored.field, ignored.reason = None, f"{lengths[ignored.index]} fields, {len(header)} expected"
    return parsed
