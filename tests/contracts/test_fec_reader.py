import datetime as dt
from pathlib import Path

import pytest

from smerisk import InputTooLarge, InvalidInput
from smerisk.contracts import fec_reader, read_fec

HEADER = (
    "JournalCode\tJournalLib\tEcritureNum\tEcritureDate\tCompteNum\tCompteLib\tCompAuxNum\tCompAuxLib\t"
    "PieceRef\tPieceDate\tEcritureLib\tDebit\tCredit\tEcritureLet\tDateLet\tValidDate\tMontantdevise\tIdevise"
)
LINES = [
    "	".join(cells)
    for cells in (
        ["VT", "Ventes", "118", "20260131", "411000", "Clients", "C042", "Customer 42", "F118", "20260131"]
        + ["Invoice 118", "1200,00", "0,00", "AA", "20260305", "20260201", "", ""],
        ["VT", "Ventes", "118", "20260131", "706000", "Prestations", "", "", "F118", "20260131"]
        + ["Invoice 118", "0,00", "1000,00", "", "", "20260201", "", ""],
        ["VT", "Ventes", "118", "20260131", "445710", "TVA collectée", "", "", "F118", "20260131"]
        + ["Invoice 118", "0,00", "200,00", "", "", "20260201", "", ""],
    )
]


def fec(header: str = HEADER, lines: list[str] = LINES, separator: str = "\t", encoding: str = "utf-8") -> bytes:
    return "\r\n".join(
        [header.replace("\t", separator), *(line.replace("\t", separator) for line in lines), ""]
    ).encode(encoding)


def test_reads_a_tab_separated_fec() -> None:
    parsed = read_fec(fec())
    assert parsed.n_ignored == 0 and len(parsed.records) == 3
    first = parsed.records[0]
    assert (first.account, first.auxiliary_account, first.debit) == ("411000", "C042", 1200.0)
    assert (first.lettering, first.lettering_date) == ("AA", dt.date(2026, 3, 5))
    assert sum(e.balance for e in parsed.records) == pytest.approx(0)  # a balanced entry


@pytest.mark.parametrize("separator", ["|", ";"])
def test_other_separators(separator: str) -> None:
    assert len(read_fec(fec(separator=separator)).records) == 3


def test_latin1_file_and_bom() -> None:
    assert len(read_fec(fec(encoding="cp1252")).records) == 3
    assert len(read_fec(b"\xef\xbb\xbf" + fec()).records) == 3


def test_reads_from_a_path(tmp_path: Path) -> None:
    path = tmp_path / "123456789FEC20261231.txt"
    path.write_bytes(fec())
    assert len(read_fec(path).records) == 3


def test_column_names_ignore_case() -> None:
    assert len(read_fec(fec(header=HEADER.lower())).records) == 3


def test_amount_and_direction_columns() -> None:
    header = "JournalCode\tEcritureNum\tEcritureDate\tCompteNum\tMontant\tSens"
    parsed = read_fec(
        fec(header=header, lines=["BQ\t7\t20260305\t512000\t1200,00\tD", "BQ\t7\t20260305\t411000\t1200,00\tC"])
    )
    assert [(e.debit, e.credit) for e in parsed.records] == [(1200.0, 0.0), (0.0, 1200.0)]


def test_bad_lines_are_set_aside() -> None:
    bad_date = LINES[1].replace("20260131", "20261341", 1)
    shifted = LINES[2].replace("\tTVA collectée", "\tTVA\tcollectée")
    parsed = read_fec(fec(lines=[LINES[0], bad_date, "", shifted]))
    assert len(parsed.records) == 1 and parsed.n_ignored == 2
    assert [(i.index, i.field) for i in parsed.ignored] == [(1, "EcritureDate"), (2, None)]
    assert parsed.ignored[1].reason == "19 fields, 18 expected"


def test_missing_columns() -> None:
    with pytest.raises(InvalidInput) as exc:
        read_fec(fec(header=HEADER.replace("\tDebit\tCredit", "\tD\tC")))
    assert "Debit and Credit" in exc.value.errors[0].message
    with pytest.raises(InvalidInput):
        read_fec(b"just some text\nwithout columns\n")


def test_file_size_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fec_reader, "MAX_FILE_BYTES", 100)
    with pytest.raises(InputTooLarge):
        read_fec(fec())
    path = tmp_path / "fec.txt"
    path.write_bytes(fec())
    with pytest.raises(InputTooLarge):
        read_fec(path)
